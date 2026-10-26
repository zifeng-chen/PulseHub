"""账单 / 聊天记录智能解析（24 §7 人工审核式导入的「对话式」扩展）。

把一段自然语言（聊天记录、随手记、账单文本）解析成「待确认草稿建议」：
- **确定性规则优先**（金额、类型、日期、账户/分类关键词），模型不可用时照常工作；
- 输出仅为建议，**绝不直接入账**——用户在前端确认后才创建草稿事件；
- 每条建议带置信度与错误说明，低置信度需人工核对，符合「AI 不越权写库」边界。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import CurrentUser
from app.core.timeutil import local_iso
from app.core.ids import utc_iso
from app.core.money import fmt_amount
from app.db.models.business import Account
from app.db.models.config import Category

# 类型关键词（命中即判定收入/支出；都未命中时默认支出）
_INCOME_HINTS = ("收入", "收到", "收款", "到账", "工资", "薪水", "薪资", "报销", "退款",
                 "利息", "红包", "分红", "租金", "返现", "补贴", "奖金", "赚")
_EXPENSE_HINTS = ("支出", "花费", "花了", "买了", "买", "付", "支付", "消费", "充值",
                  "餐", "吃饭", "购物", "打车", "加油", "缴费", "话费", "还款", "药",
                  "医", "超市", "外卖", "房租", "水电", "物业")

_AMOUNT_RE = re.compile(r"(?:¥|￥|\$)?\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块|圆|人民币)?")
_DATE_ISO_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_DATE_CN_RE = re.compile(r"(\d{1,2})月(\d{1,2})日")
_RELATIVE_DAYS = (("今天", 0), ("刚才", 0), ("昨天", -1), ("前天", -2), ("大前天", -3))


async def _load_refs(db: AsyncSession, household_id: str) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """账户名 → id；分类名 → (id, domain)。仅取启用项。"""
    accounts = (await db.execute(
        select(Account).where(Account.household_id == household_id, Account.deleted_at.is_(None))
    )).scalars().all()
    categories = (await db.execute(
        select(Category).where(Category.household_id == household_id, Category.deleted_at.is_(None))
    )).scalars().all()
    acc_map = {a.name: a.id for a in accounts if a.status == "active"}
    cat_map = {c.name: (c.id, c.domain) for c in categories if c.status == "active"}
    return acc_map, cat_map


def _parse_line(
    line: str, today: datetime,
    acc_map: dict[str, str], cat_map: dict[str, tuple[str, str]],
) -> dict | None:
    original = line.strip()
    if not original:
        return None
    text = original
    errors: list[str] = []

    # —— 日期 ——
    occurred: datetime | None = None
    m = _DATE_ISO_RE.search(text)
    if m:
        try:
            occurred = today.replace(year=int(m.group(1)), month=int(m.group(2)),
                                     day=int(m.group(3)))
        except ValueError:
            errors.append("日期不合法")
        text = _DATE_ISO_RE.sub(" ", text)
    else:
        m = _DATE_CN_RE.search(text)
        if m:
            try:
                occurred = today.replace(month=int(m.group(1)), day=int(m.group(2)))
            except ValueError:
                errors.append("日期不合法")
            text = _DATE_CN_RE.sub(" ", text)
        else:
            for kw, delta in _RELATIVE_DAYS:
                if kw in text:
                    occurred = today + timedelta(days=delta)
                    text = text.replace(kw, " ")
                    break

    # —— 类型 ——
    if any(k in original for k in _INCOME_HINTS):
        event_type = "income"
    elif any(k in original for k in _EXPENSE_HINTS):
        event_type = "expense"
    else:
        event_type = "expense"

    # —— 金额（取行内最大数字）——
    amount: Decimal | None = None
    found = _AMOUNT_RE.findall(text)
    if found:
        try:
            amount = max(Decimal(a) for a in found)
        except InvalidOperation:
            amount = None
        text = _AMOUNT_RE.sub(" ", text, count=1)
    if amount is None or amount <= 0:
        errors.append("未识别到金额，请手动填写")
        amount = None

    # —— 账户 / 分类（按名称子串匹配）——
    account_id = account_name = None
    for name, aid in acc_map.items():
        if name and name in original:
            account_id, account_name = aid, name
            break
    category_id = category_name = None
    for name, (cid, domain) in cat_map.items():
        if name and name in original:
            if domain == event_type:  # 分类域必须与收/支一致
                category_id, category_name = cid, name
                break

    # —— 标题（清理后剩余文本）——
    title = re.sub(r"[，。,.、；;：:！!？?【】\[\]()（）\s]+", " ", text).strip()
    if not title:
        title = "支出" if event_type == "expense" else "收入"
    if len(title) > 60:
        title = title[:60]

    # —— 置信度 ——
    if amount is not None and occurred is not None:
        confidence = "high"
    elif amount is not None:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "raw": original,
        "event_type": event_type,
        "title": title,
        "amount": fmt_amount(amount) if amount is not None else "",
        "occurred_at": local_iso(occurred) if occurred else None,
        "account_id": account_id,
        "account_name": account_name,
        "category_id": category_id,
        "category_name": category_name,
        "confidence": confidence,
        "errors": errors,
        "ok": amount is not None and not errors,
    }



# ---------------- 本地 AI 解析 ----------------
# 用户要求：对话式录入直接走本地 AI（不新增入口，替换原规则解析）。
# 结构化输出只有 JSON 好校验；模型不可用或输出不合法时**自动退回规则解析**，
# 保证「粘一段话点解析」永远不会因为 AI 挂了而完全不可用。

_AI_SYSTEM = (
    "你是家庭记账 App 的文本解析器。把用户给的文本拆成一条条记账条目。\n"
    "只输出一个 JSON 数组，不要解释、不要 Markdown 代码块、不要多余文字。\n"
    "每个元素包含字段：date / type / title / amount / account / category。\n"
    "- date：文本写成 YYYY-MM-DD 就照抄；写成「今天/昨天/前天/刚才」就原样输出这个词；"
    "没提日期填 null。**不要自己算相对日期**（模型算日期容易错，后端会统一换算）。\n"
    "- type：只能是 income 或 expense。\n"
    "- title：不超过 20 字的简短说明。\n"
    "- amount：纯数字字符串，不带货币符号与千分位。\n"
    "- account / category：只有文本里明确提到、且能在下面清单里对上时才填，否则填 null，"
    "不要猜测用户的账户或分类。\n"
    "示例：[{\"date\":\"2026-10-20\",\"type\":\"expense\",\"title\":\"超市采购\",\"amount\":\"128\",\"account\":\"工资卡\",\"category\":\"日用百货\"}]"
)

_AI_TYPE_MAP = {
    "income": "income", "收入": "income", "进账": "income", "收": "income",
    "expense": "expense", "支出": "expense", "花费": "expense", "付": "expense",
}


def _extract_json_array(answer: str) -> list | None:
    """从模型输出里抠出 JSON 数组：容忍 ```json 包裹、前后废话。"""
    text = (answer or "").strip()
    if not text:
        return None
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            chunk = part.strip()
            if chunk.startswith("json"):
                chunk = chunk[4:].strip()
            if chunk.startswith("["):
                text = chunk
                break
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, list) else None


def _normalize_date(value, today: datetime) -> tuple[datetime | None, bool]:
    """返回（日期, 是否合法）。支持 YYYY-MM-DD / X月Y日 / 今天·昨天·前天 / null。"""
    if value in (None, "", "null", "None"):
        return None, True
    raw = str(value).strip()
    for kw, delta in _RELATIVE_DAYS:
        if kw in raw:
            return today + timedelta(days=delta), True
    m = _DATE_ISO_RE.search(raw)
    if m:
        try:
            return today.replace(year=int(m.group(1)), month=int(m.group(2)),
                                 day=int(m.group(3))), True
        except ValueError:
            return None, False
    m = _DATE_CN_RE.search(raw)
    if m:
        try:
            return today.replace(month=int(m.group(1)), day=int(m.group(2))), True
        except ValueError:
            return None, False
    return None, False


def _item_from_ai(
    row: dict, index: int, raw_lines: list[str], today: datetime,
    acc_map: dict[str, str], cat_map: dict[str, tuple[str, str]], tz,
) -> dict:
    """把模型给的一条 JSON 规整成与规则解析完全一致的 item（前端无需改结构）。"""
    errors: list[str] = []
    original = raw_lines[index].strip() if index < len(raw_lines) else ""

    event_type = _AI_TYPE_MAP.get(str(row.get("type") or "").strip().lower(), "")
    if not event_type:
        # 模型给了奇怪的类型值时，用原文再判一次，最后兜底支出
        event_type = "income" if any(k in original for k in _INCOME_HINTS) else "expense"

    amount: Decimal | None = None
    found = _AMOUNT_RE.findall(str(row.get("amount") or ""))
    if not found:
        found = _AMOUNT_RE.findall(original)
    if found:
        try:
            amount = max(Decimal(a) for a in found)
        except InvalidOperation:
            amount = None
    if amount is None or amount <= 0:
        errors.append("未识别到金额，请手动填写")
        amount = None

    occurred, date_ok = _normalize_date(row.get("date"), today)
    if not date_ok:
        errors.append("日期不合法")

    # 账户 / 分类：模型给的名字优先，其次看原文里有没有出现
    account_id = account_name = None
    for candidate in (row.get("account"), original):
        if not candidate:
            continue
        text = str(candidate).strip()
        if not text:
            continue
        for name, aid in acc_map.items():
            if name and (name == text or name in text or text in name):
                account_id, account_name = aid, name
                break
        if account_id:
            break

    category_id = category_name = None
    for candidate in (row.get("category"), original):
        if not candidate:
            continue
        text = str(candidate).strip()
        if not text:
            continue
        for name, (cid, domain) in cat_map.items():
            if name and domain == event_type and (name == text or name in text or text in name):
                category_id, category_name = cid, name
                break
        if category_id:
            break

    title = re.sub(r"[，。,.、；;：:！!？?【】\[\]()（）\s]+", " ",
                   str(row.get("title") or "")).strip()
    if not title:
        title = re.sub(r"[，。,.、；;：:！!？?【】\[\]()（）\s]+", " ", original).strip()
    if not title:
        title = "支出" if event_type == "expense" else "收入"
    if len(title) > 60:
        title = title[:60]

    if amount is not None and occurred is not None:
        confidence = "high"
    elif amount is not None:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "raw": original or title,
        "event_type": event_type,
        "title": title,
        "amount": fmt_amount(amount) if amount is not None else "",
        "occurred_at": local_iso(occurred) if occurred else None,
        "account_id": account_id,
        "account_name": account_name,
        "category_id": category_id,
        "category_name": category_name,
        "confidence": confidence,
        "errors": errors,
        "ok": amount is not None and not errors,
    }


async def _ai_parse(
    db: AsyncSession, user: CurrentUser, text: str, today: datetime,
    acc_map: dict[str, str], cat_map: dict[str, tuple[str, str]], tz,
) -> tuple[list[dict] | None, str]:
    """调本地 AI 解析；返回（条目, 失败原因）。"""
    from app.adapters import build_adapters

    local = build_adapters(db, user.household_id)["local_ai"]
    status = await local.status()
    if status.status != "connected":
        return None, f"本地 AI 未连接（{status.detail}）"

    accounts = "、".join(acc_map.keys()) or "（无）"
    categories = "、".join(f"{name}({domain})" for name, (_cid, domain) in cat_map.items()) or "（无）"
    prompt = (
        f"今天是 {today.strftime('%Y-%m-%d')}。\n"
        f"可用账户：{accounts}\n可用分类：{categories}\n\n"
        f"待解析文本：\n{text}"
    )
    messages = [
        {"role": "system", "content": _AI_SYSTEM.replace("{today}", today.strftime('%Y-%m-%d'))},
        {"role": "user", "content": prompt},
    ]
    try:
        answer = await local.complete(messages, temperature=0.1, max_tokens=1024)
    except Exception as exc:  # noqa: BLE001 - 任何失败都退回规则解析
        return None, f"本地 AI 调用失败：{str(exc)[:100]}"

    rows = _extract_json_array(answer)
    if not rows:
        return None, "本地 AI 没有返回可解析的 JSON"

    raw_lines = [ln for ln in re.split(r"[\n\r]+", text) if ln.strip()]
    items = [
        _item_from_ai(row, idx, raw_lines, today, acc_map, cat_map, tz)
        for idx, row in enumerate(rows) if isinstance(row, dict)
    ]
    if not items:
        return None, "本地 AI 返回的条目为空"
    return items, ""


async def parse_text_to_drafts(
    db: AsyncSession, user: CurrentUser, text: str, use_ai: bool = True,
) -> dict:
    """把文本解析成待确认草稿建议（不落库）。

    默认走**本地 AI**（不出网）；AI 未连接或输出不合法时自动退回确定性规则解析。
    返回里的 parser 字段告诉前端这次实际用的是哪种，便于用户判断可靠性。
    """
    from app.services.setup import current_household
    from app.services.stats import resolve_tz

    household = await current_household(db)
    tz_name = household.timezone if household else "Asia/Shanghai"
    tz = resolve_tz(tz_name)
    today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    acc_map, cat_map = await _load_refs(db, user.household_id or "")

    items: list[dict] = []
    parser = "rule"
    ai_error = ""

    if use_ai and (text or "").strip():
        ai_items, ai_error = await _ai_parse(db, user, text, today, acc_map, cat_map, tz)
        if ai_items:
            items = ai_items
            parser = "ai"

    if not items:
        for raw_line in re.split(r"[\n\r]+", text or ""):
            parsed = _parse_line(raw_line, today, acc_map, cat_map)
            if parsed is not None:
                items.append(parsed)

    if parser == "ai":
        caliber = ("本地 AI 解析：模型按语义理解文本，可处理口语化表达；金额与日期仍经过后端校验。"
                   "结果仅为草稿建议，确认后才入账。")
    else:
        caliber = ("规则解析（本地 AI 不可用时的兜底）：类型按关键词、金额取行内最大数字、"
                   "日期支持 YYYY-MM-DD / X月Y日 / 今天昨天前天；账户与分类按名称匹配。"
                   "结果仅为草稿建议，确认后才入账。")

    return {
        "items": items,
        "total": len(items),
        "ok": sum(1 for i in items if i["ok"]),
        "need_review": sum(1 for i in items if not i["ok"]),
        "parser": parser,
        "parser_label": "本地 AI" if parser == "ai" else "规则解析",
        "ai_error": ai_error,
        "caliber": caliber,
    }
