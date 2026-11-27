"""邮件记账：IMAP 拉取招商信用卡消费提醒，直接入账。

招商「每日信用管家」邮件是**一天一封、正文含当日多笔明细**，格式形如：

    2026/11/26  您的消费明细如下：
    00:35:07 CNY 4.00 尾号0616 消费 支付宝-苏州轨道交通运营有限公司

所以解析的单位是「明细行」，不是「一封邮件一笔」。

入账口径（用户确认，2026-11-27）：
- 消费 / 取现：直接写成 confirmed 的支出事件，无需手动确认；
- 退货：先按「同卡 + 同金额 + 退款之前」找原消费，对上就生成关联原支出的
  refund 事件（资金回到卡里、统计上冲减支出），对不上则按独立退款入账；
- 还款 / 转账：跳过，信用卡还款在项目里是「扣款账户 → 信用卡」的内部转账，
  由手动或账单对账处理，邮件里再记一次会重复。
"""
from __future__ import annotations

import asyncio
import email
import hashlib
import imaplib
import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal
from email.header import decode_header

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id
from app.core.timeutil import local_now
from app.db.models.business import Account, BusinessEvent, EventMovement
from app.db.models.config import MailInboxConfig

logger = logging.getLogger(__name__)

CMB_SENDER = "ccsvc@message.cmbchina.com"
DEFAULT_HOST = "imap.qq.com"
DEFAULT_PORT = 993
DEFAULT_TIMES = "09:00,14:00,21:00"

# 00:35:07 CNY 4.00 尾号0616 消费 支付宝-苏州轨道交通运营有限公司
# 正文被压成一行，商户名必须截止到「下一个时间点」，否则会把后续明细一起吞掉
_LINE_RE = re.compile(
    r"(?P<time>\d{2}:\d{2}:\d{2})\s*CNY\s*(?P<amount>[\d,]+\.\d{2})\s*"
    r"尾号(?P<last4>\d{4})\s*(?P<kind>消费|退货|还款|取现|转账)?\s*"
    r"(?P<merchant>.{0,80}?)(?=\s+\d{2}:\d{2}:\d{2}\s|\s*$)"
)
# 正文里的对账日：2026/11/26 您的消费明细如下
_DATE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")
_AVAILABLE_RE = re.compile(r"可用额度\s*￥?\s*([\d,]+\.\d{2})")
_TAG_RE = re.compile(r"<[^>]+>")


def _decode(value: str | None) -> str:
    if not value:
        return ""
    out = ""
    for text, charset in decode_header(value):
        if isinstance(text, bytes):
            out += text.decode(charset or "utf-8", "replace")
        else:
            out += text
    return out


def _plain_body(msg: email.message.Message) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() in ("text/plain", "text/html"):
            raw = part.get_payload(decode=True) or b""
            text = raw.decode(part.get_content_charset() or "utf-8", "replace")
            text = _TAG_RE.sub(" ", text).replace("&nbsp;", " ")
            return re.sub(r"\s+", " ", text)
    return ""


def parse_cmb_daily(body: str) -> dict:
    """解析「每日信用管家」：返回 {date, available_limit, items[]}。"""
    date_match = _DATE_RE.search(body)
    bill_date = None
    if date_match:
        y, m, d = (int(x) for x in date_match.groups())
        bill_date = datetime(y, m, d)

    available = None
    limit_match = _AVAILABLE_RE.search(body)
    if limit_match:
        available = Decimal(limit_match.group(1).replace(",", ""))

    items: list[dict] = []
    for match in _LINE_RE.finditer(body):
        merchant = (match.group("merchant") or "").strip(" 　|").strip()
        merchant = merchant[:40].strip()
        items.append({
            "time": match.group("time"),
            "amount": Decimal(match.group("amount").replace(",", "")),
            "last4": match.group("last4"),
            "kind": match.group("kind") or "消费",
            "merchant": merchant or "未识别商户",
        })
    return {"date": bill_date, "available_limit": available, "items": items}


def _fetch_mails(email_addr: str, auth_code: str, host: str, port: int,
                 last_uid: str | None, limit: int = 30) -> list[dict]:
    """同步拉取（imapilib 是阻塞库，外层用 to_thread 调用）。"""
    client = imaplib.IMAP4_SSL(host, port)
    try:
        client.login(email_addr, auth_code)
        client.select("INBOX")
        criteria = f'(FROM "{CMB_SENDER}")'
        typ, data = client.search(None, criteria)
        if typ != "OK":
            return []
        uids = [u.decode() for u in data[0].split()]
        if last_uid:
            uids = [u for u in uids if int(u) > int(last_uid)]
        uids = uids[-limit:]

        mails: list[dict] = []
        for uid in uids:
            typ, msg_data = client.fetch(uid.encode(), "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            mails.append({
                "uid": uid,
                "subject": _decode(msg.get("Subject")),
                "body": _plain_body(msg),
            })
        return mails
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass


async def _config(db: AsyncSession, household_id: str) -> MailInboxConfig | None:
    return (
        await db.execute(
            select(MailInboxConfig).where(
                MailInboxConfig.household_id == household_id,
                MailInboxConfig.deleted_at.is_(None),
            )
        )
    ).scalars().first()


def serialize_config(cfg: MailInboxConfig | None) -> dict:
    if cfg is None:
        return {
            "email": "", "auth_code_masked": "", "imap_host": DEFAULT_HOST,
            "imap_port": DEFAULT_PORT, "fetch_times": DEFAULT_TIMES,
            "enabled": False, "last_fetch_at": None, "last_status": "unconfigured",
            "last_status_label": "未配置", "last_error": None,
            "sender": CMB_SENDER,
        }
    labels = {"ok": "正常", "error": "拉取失败", "unconfigured": "未配置", "running": "拉取中"}
    return {
        "email": cfg.email or "",
        "auth_code_masked": "••••••••" if cfg.auth_code_cipher else "",
        "imap_host": cfg.imap_host,
        "imap_port": cfg.imap_port,
        "fetch_times": cfg.fetch_times,
        "enabled": bool(cfg.enabled),
        "last_fetch_at": cfg.last_fetch_at.isoformat() if cfg.last_fetch_at else None,
        "last_status": cfg.last_status,
        "last_status_label": labels.get(cfg.last_status, cfg.last_status),
        "last_error": cfg.last_error,
        "sender": CMB_SENDER,
    }


async def save_config(db: AsyncSession, user, payload: dict) -> MailInboxConfig:
    cfg = await _config(db, user.household_id or "")
    if cfg is None:
        cfg = MailInboxConfig(id=new_id(), household_id=user.household_id or "")
        db.add(cfg)
        await db.flush()

    email_addr = (payload.get("email") or "").strip()
    if email_addr and "@" not in email_addr:
        raise AppError(ErrorCode.VALIDATION_ERROR, "邮箱地址格式不正确",
                       field_errors={"email": "请填写完整邮箱地址"})
    if email_addr:
        cfg.email = email_addr

    code = (payload.get("auth_code") or "").strip()
    if code:
        cfg.auth_code_cipher = encrypt_secret(code)

    for key in ("imap_host", "imap_port", "fetch_times"):
        if payload.get(key) not in (None, ""):
            setattr(cfg, key, payload[key])
    if "enabled" in payload:
        cfg.enabled = bool(payload["enabled"])

    if cfg.email and cfg.auth_code_cipher and cfg.last_status == "unconfigured":
        cfg.last_status = "ok"
    cfg.updated_by = user.user_id
    await db.flush()
    return cfg


async def test_connection(db: AsyncSession, user) -> dict:
    cfg = await _config(db, user.household_id or "")
    if cfg is None or not cfg.email or not cfg.auth_code_cipher:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请先保存邮箱地址与授权码")
    code = decrypt_secret(cfg.auth_code_cipher) or ""
    try:
        mails = await asyncio.to_thread(_fetch_mails, cfg.email, code, cfg.imap_host, cfg.imap_port, None, 1)
    except Exception as exc:  # noqa: BLE001
        cfg.last_status = "error"
        cfg.last_error = str(exc)[:500]
        await db.flush()
        raise AppError(ErrorCode.VALIDATION_ERROR, f"连接失败：{exc}") from exc
    cfg.last_status = "ok"
    cfg.last_error = None
    await db.flush()
    return {"ok": True, "sample": mails[0]["subject"] if mails else ""}


def _norm_merchant(text: str) -> str:
    return re.sub(r"[\s\-—_·・|]+", "", text or "")


def _merchant_similar(left: str, right: str) -> bool:
    """商户名比对：消费与退货两封信里可能略有差异，用包含关系兜底。"""
    a, b = _norm_merchant(left), _norm_merchant(right)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 4 and short in long


async def _find_refund_origin(db: AsyncSession, user, account: Account, item: dict,
                              occurred: datetime) -> BusinessEvent | None:
    """找退货对应的原消费：同卡、同金额、发生在退款之前（近 90 天）。

    商户名对得上优先；金额相同但商户不同且只此一笔时才敢对——多个不同商户的候选
    宁可记成独立退款，也不能把别人的消费抵掉。
    """
    rows = list((await db.execute(
        select(BusinessEvent)
        .join(EventMovement, EventMovement.event_id == BusinessEvent.id)
        .where(
            BusinessEvent.household_id == user.household_id,
            BusinessEvent.event_type == "expense",
            BusinessEvent.status == "confirmed",
            BusinessEvent.deleted_at.is_(None),
            BusinessEvent.occurred_at <= occurred,
            BusinessEvent.occurred_at >= occurred - timedelta(days=90),
            EventMovement.target_type == "account",
            EventMovement.target_id == account.id,
            EventMovement.direction == "out",
            EventMovement.deleted_at.is_(None),
            EventMovement.amount == item["amount"],
        )
        .order_by(BusinessEvent.occurred_at.desc())
    )).scalars().unique().all())
    if not rows:
        return None
    same_merchant = [row for row in rows if _merchant_similar(row.title or "", item["merchant"])]
    if same_merchant:
        return same_merchant[0]
    return rows[0] if len(rows) == 1 else None


async def _refunded_amount(db: AsyncSession, origin_id: str) -> Decimal:
    total = (await db.execute(
        select(func.coalesce(func.sum(BusinessEvent.amount), 0)).where(
            BusinessEvent.refund_of == origin_id,
            BusinessEvent.event_type == "refund",
            BusinessEvent.status != "reversed",
            BusinessEvent.deleted_at.is_(None),
        )
    )).scalar() or 0
    return Decimal(str(total))


async def fetch_and_post(db: AsyncSession, user) -> dict:
    """拉取并解析：消费直接入账，退货自动对冲。返回统计信息。"""
    cfg = await _config(db, user.household_id or "")
    if cfg is None or not cfg.email or not cfg.auth_code_cipher:
        raise AppError(ErrorCode.VALIDATION_ERROR, "邮件记账尚未配置")

    code = decrypt_secret(cfg.auth_code_cipher) or ""
    mails = await asyncio.to_thread(
        _fetch_mails, cfg.email, code, cfg.imap_host, cfg.imap_port, cfg.last_uid, 30
    )

    accounts = list((await db.execute(
        select(Account).where(Account.household_id == user.household_id,
                              Account.deleted_at.is_(None))
    )).scalars().all())
    by_last4 = {a.card_last4: a for a in accounts if a.card_last4}

    created = refunded = skipped = 0
    max_uid = cfg.last_uid
    for mail in mails:
        parsed = parse_cmb_daily(mail["body"])
        if parsed["date"] is None:
            continue
        for item in parsed["items"]:
            kind = item["kind"]
            # 还款 / 转账跳过：信用卡还款在项目里是「扣款账户 → 信用卡」的内部转账，
            # 邮件里再记一次会与手动记账重复
            if kind not in ("消费", "取现", "退货"):
                skipped += 1
                continue
            account = by_last4.get(item["last4"])
            if account is None:
                skipped += 1
                continue
            hour, minute, second = (int(x) for x in item["time"].split(":"))
            occurred = parsed["date"].replace(hour=hour, minute=minute, second=second)
            dedupe = hashlib.sha1(
                f"{occurred.isoformat()}|{item['amount']}|{item['last4']}|{item['merchant']}".encode()
            ).hexdigest()

            # 去重：同一封邮件重复拉取时，note 里已带这个 hash 就不再入库
            exists = (await db.execute(
                select(BusinessEvent.id).where(
                    BusinessEvent.household_id == user.household_id,
                    BusinessEvent.note.like(f"%{dedupe}%"),
                    BusinessEvent.deleted_at.is_(None),
                )
            )).scalars().first()
            if exists:
                skipped += 1
                continue

            if kind == "退货":
                origin = await _find_refund_origin(db, user, account, item, occurred)
                if origin is not None:
                    remaining = Decimal(str(origin.amount or 0)) - await _refunded_amount(db, origin.id)
                    if remaining < item["amount"]:
                        # 可退余额不足（部分退过），不关联原支出，按独立退款入账
                        origin = None
                note = f"邮件识别 · 尾号{item['last4']} · 退货 · {dedupe}"
                if origin is not None:
                    note += f" · 对冲原支出 {origin.event_no}"
                event = BusinessEvent(
                    id=new_id(), household_id=user.household_id,
                    event_no=f"EV{local_now().strftime('%Y%m%d')}{new_id()[:6].upper()}",
                    event_type="refund", status="confirmed",
                    occurred_at=occurred, confirmed_at=occurred,
                    amount=item["amount"], currency=account.currency,
                    original_amount=item["amount"], original_currency=account.currency,
                    base_currency=account.currency, conversion_status="same_currency",
                    account_id=account.id, title=f"退款 · {item['merchant']}",
                    source="email", note=note,
                    refund_of=origin.id if origin else None,
                    version=1, created_by=user.user_id, updated_by=user.user_id,
                )
                db.add(event)
                await db.flush()
                db.add(EventMovement(
                    id=new_id(), household_id=user.household_id, event_id=event.id,
                    target_type="account", target_id=account.id, direction="in",
                    amount=item["amount"], currency=account.currency,
                    note="信用卡退款（邮件自动对冲）" if origin else "信用卡退款（邮件）",
                    created_by=user.user_id, updated_by=user.user_id,
                ))
                refunded += 1
                continue

            event = BusinessEvent(
                id=new_id(), household_id=user.household_id,
                event_no=f"EV{local_now().strftime('%Y%m%d')}{new_id()[:6].upper()}",
                event_type="expense", status="confirmed",
                occurred_at=occurred, confirmed_at=occurred,
                amount=item["amount"], currency=account.currency,
                original_amount=item["amount"], original_currency=account.currency,
                base_currency=account.currency, conversion_status="same_currency",
                account_id=account.id, title=item["merchant"], source="email",
                note=f"邮件识别 · 尾号{item['last4']} · {kind} · {dedupe}",
                version=1, created_by=user.user_id, updated_by=user.user_id,
            )
            db.add(event)
            await db.flush()
            db.add(EventMovement(
                id=new_id(), household_id=user.household_id, event_id=event.id,
                target_type="account", target_id=account.id, direction="out",
                amount=item["amount"], currency=account.currency,
                note="信用卡消费（邮件自动入账）",
                created_by=user.user_id, updated_by=user.user_id,
            ))
            created += 1
        if max_uid is None or int(mail["uid"]) > int(max_uid):
            max_uid = mail["uid"]

    cfg.last_uid = max_uid
    cfg.last_fetch_at = local_now()
    cfg.last_status = "ok"
    cfg.last_error = None
    await db.flush()
    return {"mails": len(mails), "created": created, "refunded": refunded, "skipped": skipped}


async def due_for_fetch(db: AsyncSession, household_id: str) -> MailInboxConfig | None:
    """到点且今天该时段还没拉过，就返回配置。"""
    cfg = await _config(db, household_id)
    if cfg is None or not cfg.enabled or not cfg.auth_code_cipher:
        return None
    now = local_now()
    slots = [s.strip() for s in (cfg.fetch_times or "").split(",") if s.strip()]
    due = [s for s in slots if now.strftime("%H:%M") >= s]
    if not due:
        return None
    if cfg.last_fetch_at is not None and cfg.last_fetch_at.date() == now.date():
        last_slot = max(due) if due else ""
        if cfg.last_fetch_at.strftime("%H:%M") >= last_slot:
            return None
    return cfg
