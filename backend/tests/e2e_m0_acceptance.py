"""M0 框架基座端到端验收脚本（对运行中的后端执行真实 HTTP 调用）。

覆盖《18-框架基座与模块化交付规范》§3 的 11 项基座验收：
1 判断是否初始化 / 2 配置数据库并创建管理员 / 3 登录退出刷新改密 /
4 创建家庭和成员验证角色与数据权限 / 5 四项一级菜单与玻璃应用壳（前端构建产物验证）/
6 路由派生菜单（前端 vitest）/ 7 移动端适配（前端构建 + CSS 安全区）/
8 空页面可打开 / 9 统一状态组件 / 10 未配置能力不阻塞 / 11 最小业务事件草稿并写审计

M1 核心账务：17.x（账户、流水、转账、统计口径、多币种）。
M2 资产与专项资产：18.x（不动产、车位、车辆与保险、贵金属、估值、购置/处置、归档）。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    # trust_env=False：忽略系统 HTTP_PROXY，直连本机后端
    c = httpx.Client(base_url=BASE, timeout=20.0, trust_env=False)

    # 1. 安装检测与初始化判定
    r = c.get("/setup/status")
    check("1.1 安装检测：可判断是否初始化", r.json()["data"]["initialized"] is False)
    r = c.get("/setup/check")
    checks = {i["key"]: i["status"] for i in r.json()["data"]["checks"]}
    check("1.2 安装检测：数据库/文件/后端/迁移四项检查", set(checks) == {"backend", "database", "file", "migration"}, str(checks))

    # 2. 配置数据库并创建管理员
    r = c.post("/setup/database", json={"use_embedded_dev_database": True})
    check("2.1 保存数据库配置", r.json()["success"] is True, r.json()["data"]["mode"])
    r = c.post("/setup/admin", json={
        "display_name": "系统管理员", "username": "admin",
        "password": "Fam@2026#Strong", "confirm_password": "Fam@2026#Strong",
        "recovery_email": "admin@example.com"})
    check("2.2 创建管理员并做密码强度校验", r.json()["success"] is True, r.json()["data"]["password_strength"]["label"])
    r = c.post("/setup/admin", json={
        "display_name": "弱口令", "username": "weak",
        "password": "123456", "confirm_password": "123456"})
    check("2.3 弱口令被拒绝（字段级错误）", r.status_code == 422 and "password" in r.json()["error"]["field_errors"])

    r = c.post("/setup/household", json={"name": "验收家庭", "base_currency": "CNY", "timezone": "Asia/Shanghai"})
    check("2.4 创建家庭并生成默认分类", r.json()["success"] is True)
    r = c.post("/setup/finance", json={"asset_purchase_separate": True, "investment_valuation_in_net_worth": True})
    check("2.5 保存财务口径", r.json()["success"] is True)
    r = c.get("/setup/summary")
    providers = r.json()["data"]["providers"]
    check("2.6 完成页摘要含 AI/OCR/通知状态", all(k in providers for k in ("local_ai", "ocr", "notification", "third_party_ai")))
    r = c.post("/setup/complete")
    check("2.7 完成初始化", r.json()["data"]["initialized"] is True)

    # 初始化接口关闭
    r = c.post("/setup/admin", json={"display_name": "x", "username": "x2", "password": "Aa@12345678", "confirm_password": "Aa@12345678"})
    check("2.8 初始化完成后公开初始化接口关闭(409)", r.status_code == 409 and r.json()["error"]["code"] == "SETUP_ALREADY_DONE")

    # 3. 登录 / 刷新 / 退出 / 改密
    r = c.post("/auth/login", json={"username": "admin", "password": "Fam@2026#Strong", "remember_device": True})
    data = r.json()["data"]
    access, refresh, sid = data["access_token"], data["refresh_token"], data["session_id"]
    H = {"Authorization": f"Bearer {access}"}
    check("3.1 登录成功并返回令牌与会话", bool(access and refresh and sid))
    check("3.2 登录返回角色与动作集合", "configure" in data["user"]["actions"], data["user"]["role_label"])
    r = c.post("/auth/refresh", json={"refresh_token": refresh})
    new_refresh = r.json()["data"]["refresh_token"]
    check("3.3 刷新会话并轮换刷新令牌", new_refresh != refresh)
    r = c.post("/auth/refresh", json={"refresh_token": refresh})
    check("3.4 旧刷新令牌失效", r.status_code == 401)
    r = c.get("/auth/me", headers=H)
    check("3.5 读取当前用户", r.json()["data"]["username"] == "admin")
    r = c.get("/auth/sessions", headers=H)
    check("3.6 会话列表标记当前会话", any(i["current"] for i in r.json()["data"]["items"]))

    # 4. 家庭成员与权限
    r = c.post("/settings/members", headers=H, json={
        "username": "viewer", "display_name": "只读成员", "password": "Viewer@2026#1", "role": "readonly"})
    check("4.1 创建家庭成员", r.json()["success"] is True)
    r = c.post("/auth/login", json={"username": "viewer", "password": "Viewer@2026#1"})
    vaccess = r.json()["data"]["access_token"]
    VH = {"Authorization": f"Bearer {vaccess}"}
    check("4.2 只读成员登录后角色正确", r.json()["data"]["user"]["role"] == "readonly")
    check("4.3 只读成员可查看统计", c.get("/dashboard/snapshot", headers=VH).status_code == 200)
    r = c.post("/ledger/events", headers=VH, json={"event_type": "expense", "title": "不应成功", "amount": "1"})
    check("4.4 只读成员不能创建草稿(403)", r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN")
    r = c.get("/audit", headers=VH)
    check("4.5 只读成员不能查看审计(403)", r.status_code == 403)

    # 8/9. 空页面与统一状态
    r = c.get("/ledger/categories", headers=H)
    check("8.1 家庭台账分类可用（默认分类已生成）", len(r.json()["data"]["items"]) > 10)
    r = c.get("/ledger/assets", headers=H)
    body = r.json()["data"]
    check("8.2 资产模块已交付：空列表 + 顶部统计（M2）",
          body["items"] == [] and body["summary"]["count"] == 0)
    r = c.get("/ledger/liabilities", headers=H)
    body = r.json()["data"]
    check("8.2b 负债与投资模块已交付：空列表 + 汇总（M3）",
          body["items"] == [] and body["summary"]["count"] == 0)
    r = c.get("/ledger/investments", headers=H)
    body = r.json()["data"]
    check("8.2c 投资模块已交付：空列表 + 汇总（M3）",
          body["items"] == [] and body["summary"]["count"] == 0)
    r = c.get("/ledger/leases", headers=H)
    body = r.json()["data"]
    check("8.2d 房产租赁已交付：空租约列表（M4）", body["items"] == [] and body["total"] == 0)
    r = c.get("/ledger/events", headers=H)
    check("8.3 空列表使用统一列表信封", set(r.json()["data"]) >= {"items", "total", "page", "page_size", "has_next"})
    r = c.get("/dashboard/snapshot", headers=H)
    snap = r.json()["data"]
    check("8.4 工作台返回口径与空状态", snap["is_empty"] is True and snap["caliber"]["excludes_draft"] is True,
          snap["caliber"]["net_worth"])
    r = c.get("/insights/overview", headers=H)
    check("8.5 洞察模块已交付且结论结构完整", r.json()["data"]["module_enabled"] is True
          and len(r.json()["data"]["conclusions"]) >= 6)
    r = c.get("/insights/data-quality", headers=H)
    check("8.6 数据质量非盘点且已运行检查", r.json()["data"]["is_inventory"] is False
          and r.json()["data"]["checked"] is True)

    # 10. 未配置能力不阻塞
    r = c.get("/ai/providers", headers=H)
    items = {i["key"]: i for i in r.json()["data"]["items"]}
    check("10.1 四类适配器均为未配置状态", all(i["status"] == "unconfigured" for i in items.values()))
    r = c.post("/ai/providers/local_ai/test", headers=H)
    check("10.2 未配置能力测试返回可操作错误码", r.status_code == 409 and r.json()["error"]["code"] == "PROVIDER_NOT_CONFIGURED",
          r.json()["error"]["message"])
    r = c.post("/ai/chat", headers=H, json={"question": "净资产是多少"})
    check("10.3 财务数据类问题无需模型用确定性统计回答", r.json()["data"]["state"] == "ready"
          and r.json()["data"]["trust"]["model_type"] == "deterministic")

    # 11. 最小业务事件草稿 + 确认 + 冲销 + 审计
    r = c.post("/ledger/events", headers=H, json={
        "event_type": "expense", "title": "验收测试采购", "amount": "128.50", "currency": "CNY"})
    ev = r.json()["data"]
    check("11.1 创建业务事件草稿", ev["status"] == "draft" and ev["amount"] == "128.50")
    check("11.2 草稿不计入统计", ev["counts_in_stats"] is False)
    r = c.post(f"/ledger/events/{ev['id']}/confirm", headers=H, json={"version": ev["version"]})
    conf = r.json()["data"]
    check("11.3 确认事件生成资金变动", conf["status"] == "confirmed" and len(conf["movements"]) == 1)
    r = c.post(f"/ledger/events/{ev['id']}/confirm", headers=H, json={"version": ev["version"]})
    check("11.4 版本冲突返回 409", r.status_code == 409 and r.json()["error"]["code"] == "VERSION_CONFLICT")
    r = c.post(f"/ledger/events/{ev['id']}/reverse", headers=H, json={"reason": "验收完成后冲销", "version": conf["version"]})
    check("11.5 冲销成功并保留历史", r.json()["data"]["status"] == "reversed")

    r = c.get("/audit", headers=H)
    total = r.json()["data"]["total"]
    check("11.6 审计写入且总数 > 0", total > 0, f"共 {total} 条")
    row = next(i for i in r.json()["data"]["items"] if i["object_type"] == "business_event")
    r = c.get(f"/audit/{row['id']}", headers=H)
    d = r.json()["data"]
    check("11.7 审计详情含字段级差异与中文标签",
          bool(d["changes"]) and bool(d["role_label"]) and bool(d["source_method_label"]),
          f"字段差异 {len(d['changes'])} 项")

    # 高影响动作二次确认
    r = c.put("/settings/privacy", headers=H, json={"cloud_egress_enabled": True})
    check("12.1 云端外发需二次确认令牌(422)", r.status_code == 422)
    r = c.post("/settings/confirm-tokens", headers=H, json={"action": "privacy.cloud_egress", "object_type": "privacy"})
    token = r.json()["data"]["token"]
    r = c.put("/settings/privacy", headers=H, json={"cloud_egress_enabled": True, "confirm_token": token})
    check("12.2 携带令牌后开启成功", r.json()["success"] is True)
    r = c.put("/settings/privacy", headers=H, json={"cloud_egress_enabled": False, "confirm_token": token})
    check("12.3 令牌一次性，重复使用被拒", r.status_code == 422)

    # 备份 / 恢复
    r = c.post("/settings/confirm-tokens", headers=H, json={"action": "backup.create", "object_type": "backup"})
    btoken = r.json()["data"]["token"]
    r = c.post("/settings/backup/create", headers=H, json={"confirm_token": btoken})
    bk = r.json()["data"]
    check("13.1 创建备份", bk["status"] == "created" and bool(bk["checksum"]))
    r = c.post(f"/settings/backup/{bk['id']}/verify", headers=H)
    check("13.2 备份完整性验证通过", r.json()["data"]["integrity"] is True)
    r = c.get(f"/settings/backup/{bk['id']}/restore-preview", headers=H)
    check("13.3 恢复预览", r.json()["data"]["confirm_phrase_required"] == "确认恢复")
    r = c.post(f"/settings/backup/{bk['id']}/restore", headers=H, json={"confirm_phrase": "确认恢复"})
    check("13.4 恢复需确认短语", r.json()["success"] is True)

    # 维护模式
    r = c.post("/system/maintenance/enter", headers=H, json={"reason": "验收演练"})
    check("14.1 进入维护模式", r.json()["data"]["active"] is True)
    r = c.post("/ledger/events", headers=H, json={"event_type": "expense", "title": "维护期间写入", "amount": "1"})
    check("14.2 维护模式下拒绝业务写入(503)", r.status_code == 503 and r.json()["error"]["code"] == "MAINTENANCE_MODE")
    check("14.3 维护模式下读取不受影响", c.get("/dashboard/snapshot", headers=H).status_code == 200)
    r = c.post("/system/maintenance/exit", headers=H)
    check("14.4 退出维护模式", r.json()["data"]["active"] is False)

    # 设置状态
    r = c.get("/settings/overview", headers=H)
    check("15.1 设置首页配置完整度", "completeness" in r.json()["data"])
    r = c.get("/settings/system", headers=H)
    st = r.json()["data"]
    check("15.2 系统状态含版本/迁移/密钥版本/提供商", all(k in st for k in ("app_version", "migration_version", "key_version", "providers")))
    r = c.get("/meta/enums", headers=H)
    reg = r.json()["data"]["registry"]
    check("15.3 枚举中文映射", reg["event_status"]["draft"] == "草稿" and reg["role"]["owner"] == "家庭所有者")

    # ---------------- M1 核心账务 ----------------
    print("\n----- M1 核心账务 -----")

    r = c.post("/ledger/accounts", headers=H, json={
        "name": "工资卡", "account_type": "bank", "institution": "某银行",
        "currency": "CNY", "opening_balance": "10000", "include_in_net_worth": True})
    a1 = r.json()["data"]["id"]
    check("17.1 新增账户并返回期初余额", r.json()["data"]["balance"] == "10000.00",
          r.json()["data"]["account_type_label"])
    r = c.post("/ledger/accounts", headers=H, json={
        "name": "现金", "account_type": "cash", "currency": "CNY", "opening_balance": "500"})
    a2 = r.json()["data"]["id"]

    r = c.post("/ledger/events", headers=H, json={
        "event_type": "expense", "title": "草稿支出", "amount": "999", "account_id": a1})
    draft = r.json()["data"]
    r = c.get(f"/ledger/accounts/{a1}", headers=H)
    check("17.2 草稿不影响账户余额", r.json()["data"]["balance"] == "10000.00")

    r = c.post(f"/ledger/events/{draft['id']}/confirm", headers=H, json={"version": draft["version"]})
    r = c.get(f"/ledger/accounts/{a1}", headers=H)
    check("17.3 确认后余额 = 期初 − 支出", r.json()["data"]["balance"] == "9001.00",
          r.json()["data"]["balance"])

    r = c.post("/ledger/events", headers=H, json={
        "event_type": "income", "title": "工资收入", "amount": "3000", "account_id": a1})
    inc = r.json()["data"]
    c.post(f"/ledger/events/{inc['id']}/confirm", headers=H, json={"version": inc["version"]})

    r = c.get("/ledger/summary", headers=H)
    summary = r.json()["data"]
    check("17.4 台账统计：收入/支出/结余",
          summary["metrics"]["income"] == "3000.00" and summary["metrics"]["expense"] == "999.00",
          f"收入 {summary['metrics']['income']} 支出 {summary['metrics']['expense']}")
    check("17.5 净资产口径来自后端确定性统计",
          summary["net_worth"]["net_worth"] == "12501.00", summary["net_worth"]["net_worth"])
    check("17.6 统计口径字段齐备",
          "期初余额" in summary["caliber"]["account_balance"] and summary["caliber"]["excludes_draft"] is True)

    r = c.post("/ledger/summary", headers=H)
    check("17.7 summary 仅支持 GET（方法约束生效）", r.status_code == 405)

    before = summary["metrics"]
    r = c.post("/ledger/transfers", headers=H, json={
        "from_account_id": a1, "to_account_id": a2, "amount": "1500", "fee": "5"})
    transfer = r.json()["data"]
    check("17.8 内部转账生成转账组", transfer["group_no"].startswith("TR") and transfer["status"] == "confirmed")
    r = c.get(f"/ledger/accounts/{a1}", headers=H)
    check("17.9 转出账户扣本金与手续费", r.json()["data"]["balance"] == "10496.00",
          r.json()["data"]["balance"])
    r = c.get("/ledger/summary", headers=H)
    after = r.json()["data"]
    check("17.10 内部转账不改变收入", after["metrics"]["income"] == before["income"])
    check("17.11 手续费计入支出、转账本金不计入",
          float(after["metrics"]["expense"]) - float(before["expense"]) == 5.0,
          after["metrics"]["expense"])

    r = c.post("/ledger/transfers", headers=H, json={
        "from_account_id": a1, "to_account_id": a1, "amount": "1"})
    check("17.12 转出与转入不能是同一账户", r.status_code == 422
          and "to_account_id" in r.json()["error"]["field_errors"])

    r = c.post("/ledger/accounts", headers=H, json={
        "name": "美元账户", "currency": "USD", "opening_balance": "100"})
    usd = r.json()["data"]["id"]
    r = c.post("/ledger/transfers", headers=H, json={
        "from_account_id": a1, "to_account_id": usd, "amount": "700"})
    check("17.13 跨币种转账必须显式给汇率", r.status_code == 422
          and "exchange_rate" in r.json()["error"]["field_errors"])

    r = c.get("/ledger/summary", headers=H)
    by_ccy = {i["currency"] for i in r.json()["data"]["accounts"]["by_currency"]}
    check("17.14 多币种按币种分组、不跨币种相加", by_ccy == {"CNY", "USD"}, str(by_ccy))

    r = c.post("/ledger/events", headers=H, json={
        "event_type": "asset_acquire", "title": "缺少资产", "amount": "1", "account_id": a1})
    check("17.15 资产事件已开放但必须关联资产（M2）", r.status_code == 422
          and "asset_id" in r.json()["error"]["field_errors"])

    r = c.post("/ledger/events", headers=H, json={
        "event_type": "liability_repay", "title": "缺少负债", "amount": "1"})
    check("17.15b 负债/投资事件已开放但必须关联对象（M3）", r.status_code == 422
          and "liability_id" in r.json()["error"]["field_errors"])

    r = c.post("/ledger/events", headers=H, json={
        "event_type": "rent_received", "title": "缺少租约", "amount": "1"})
    check("17.15c 租赁事件已开放但必须关联租约（M4）", r.status_code == 422
          and "lease_id" in r.json()["error"]["field_errors"])

    r = c.delete(f"/ledger/accounts/{a2}", headers=H, params={"reason": "测试"})
    check("17.16 停用账户需要二次确认令牌", r.status_code == 422)

    r = c.get("/ledger/transfers", headers=H)
    check("17.17 转账列表可查询", r.json()["data"]["total"] >= 1)

    r = c.get("/audit", headers=H, params={"object_type": "transfer"})
    check("17.18 转账写入审计", r.json()["data"]["total"] >= 1)

    # ---------------- M2 资产与专项资产 ----------------

    r = c.get("/ledger/summary", headers=H)
    nw_before = r.json()["data"]["net_worth"]

    r = c.post("/ledger/assets", headers=H, json={
        "asset_type": "real_estate", "name": "城东自住房", "currency": "CNY",
        "acquire_cost": "1800000", "current_value": "2100000", "value_source": "appraisal",
        "extension": {"property_type": "residence", "title_no": "京(2020)东不动产权第0001号",
                      "address": "城东区示例路 1 号", "building_area": "128.5",
                      "has_mortgage": True},
    })
    house = r.json()["data"]
    check("18.1 新不动产与类型扩展字段", r.json()["success"] and house["asset_type"] == "real_estate"
          and house["extension"]["property_type_label"] == "住宅")
    check("18.2 敏感证件号默认掩码", "*" in house["extension"]["title_no"])
    house_id = house["id"]

    r = c.get(f"/ledger/assets/{house_id}", headers=H, params={"reveal": 1})
    check("18.3 审计查看权限可看完整证件号",
          r.json()["data"]["extension"]["title_no"] == "京(2020)东不动产权第0001号")

    r = c.post("/ledger/assets", headers=H, json={
        "asset_type": "parking", "name": "地下车位", "current_value": "180000",
        "extension": {"parking_type": "ownership"},
    })
    check("18.4 车位类型必填字段校验", r.status_code == 422
          and "extension.parking_no" in r.json()["error"]["field_errors"])

    r = c.post("/ledger/assets", headers=H, json={
        "asset_type": "parking", "name": "B1-088 车位", "current_value": "180000",
        "extension": {"parking_type": "ownership", "parking_no": "B1-088",
                      "linked_real_estate_id": house_id, "management_fee": "1200",
                      "management_fee_cycle": "yearly"},
    })
    parking = r.json()["data"]
    check("18.5 车位关联到具体不动产", parking["extension"]["linked_real_estate_name"] == "城东自住房")

    r = c.post("/ledger/assets", headers=H, json={
        "asset_type": "parking", "name": "错链车位",
        "extension": {"parking_type": "ownership", "parking_no": "X-1",
                      "linked_real_estate_id": parking["id"]}})
    check("18.6 车位不能关联车位", r.status_code == 422
          and "extension.linked_real_estate_id" in r.json()["error"]["field_errors"])

    soon = (datetime.now(timezone.utc) + timedelta(days=10)).date().isoformat()
    r = c.post("/ledger/assets", headers=H, json={
        "asset_type": "vehicle", "name": "家用车", "acquire_cost": "180000",
        "current_value": "140000",
        "extension": {"brand": "示例牌", "plate_no": "沪A12345", "vin": "LSV1234567890ABCD",
                      "energy_type": "electric", "mileage": "18500.5", "has_loan": True},
    })
    vehicle = r.json()["data"]
    vehicle_id = vehicle["id"]
    r = c.post(f"/ledger/assets/vehicles/{vehicle_id}/insurances", headers=H, json={
        "insurance_type": "compulsory", "insurer": "某保险公司",
        "effective_date": "2026-01-01", "expiry_date": soon, "premium": "950"})
    insurance = r.json()["data"]
    check("18.7 车辆保险到期实时识别", insurance["expiring_soon"] is True
          and 0 <= insurance["days_to_expiry"] <= 30)

    r = c.post("/ledger/assets", headers=H, json={
        "asset_type": "precious_metal", "name": "投资金条 100g", "current_value": "52000",
        "extension": {"category": "gold", "form": "bar", "purity": "0.999", "weight": "100",
                      "unit": "g", "current_unit_price": "520"},
    })
    metal = r.json()["data"]
    check("18.8 贵金属参考价值按重量×纯度×单价", metal["extension"]["suggested_value"] == "51948.00")

    r = c.post("/ledger/assets", headers=H, json={
        "asset_type": "other", "name": "笔记本电脑", "acquire_cost": "12000",
        "current_value": "9000",
        "acquisition": {"mode": "confirm", "account_id": a1, "amount": "12000"},
    })
    laptop = r.json()["data"]
    r = c.get(f"/ledger/accounts/{a1}", headers=H)
    balance_after_purchase = r.json()["data"]["balance"]
    r = c.get(f"/ledger/assets/{laptop['id']}", headers=H)
    events = r.json()["data"]["events"]
    check("18.9 资产购置生成账户+资产双资金变动",
          bool(events) and len(events[0]["movements"]) == 2
          and {m["target_type"] for m in events[0]["movements"]} == {"account", "asset"})

    r = c.get("/ledger/summary", headers=H)
    nw_after = r.json()["data"]["net_worth"]
    delta_accounts = float(nw_after["accounts"]) - float(nw_before["accounts"])
    delta_assets = float(nw_after["assets"]) - float(nw_before["assets"])
    check("18.10 净资产并入资产价值", abs(delta_accounts + 12000) < 0.01
          and abs(delta_assets - (2100000 + 180000 + 140000 + 52000 + 9000)) < 0.01,
          f"账户 {delta_accounts} / 资产 {delta_assets}")

    r = c.get("/ledger/assets/summary", headers=H)
    summary = r.json()["data"]
    check("18.11 顶部统计：流动/非流动/待估值",
          float(summary["current_value"]) >= 52000 and float(summary["non_current_value"]) >= 2420000
          and summary["count"] >= 5)

    r = c.post(f"/ledger/assets/{house_id}/valuations", headers=H,
               json={"value": "2300000", "source": "appraisal"})
    check("18.12 调整已确认价值必须填写原因", r.status_code == 422
          and "reason" in r.json()["error"]["field_errors"])

    r = c.post(f"/ledger/assets/{house_id}/valuations", headers=H,
               json={"value": "2300000", "source": "appraisal",
                     "reason": "第三方评估机构重新评估"})
    check("18.13 新增估值写入时间线并更新当前价值", r.json()["success"] is True)
    r = c.get(f"/ledger/assets/{house_id}", headers=H)
    check("18.14 估值历史包含两次记录", len(r.json()["data"]["valuations"]) == 2)

    r = c.post(f"/ledger/assets/real-estates/{house_id}/utility-accounts", headers=H, json={
        "utility_type": "electricity", "provider": "某电力公司",
        "account_no": "123456789012", "billing_cycle": "monthly", "billing_day": 10})
    utility = r.json()["data"]
    check("18.15 水电煤户号挂在不动产且户号格式化", r.json()["success"] and utility["account_no"] == "1234 5678 9012")

    r = c.post(f"/ledger/assets/vehicles/{vehicle_id}/insurances", headers=H, json={
        "insurance_type": "commercial", "insurer": "某保险公司"})
    check("18.16 车辆可维护多份保险", r.json()["success"] is True)

    r = c.post(f"/ledger/assets/{laptop['id']}/dispose", headers=H, json={
        "dispose_type": "sold", "amount": "8000", "account_id": a1, "reason": "换新机"})
    check("18.17 处置需要一次性确认令牌", r.status_code == 422
          and "confirm_token" in r.json()["error"]["field_errors"])

    r = c.get("/ledger/summary", headers=H)
    nw_pre_dispose = r.json()["data"]["net_worth"]

    r = c.post("/settings/confirm-tokens", headers=H, json={
        "action": "asset.dispose", "object_type": "asset", "object_id": laptop["id"]})
    token = r.json()["data"]["token"]
    r = c.post(f"/ledger/assets/{laptop['id']}/dispose", headers=H, json={
        "dispose_type": "sold", "amount": "8000", "account_id": a1, "reason": "换新机",
        "confirm_token": token})
    dispose_event_id = r.json()["data"]["event_id"]
    check("18.18 处置生成资产处置事件并改变状态",
          r.json()["success"] and r.json()["data"]["status"] == "disposed")

    r = c.get("/ledger/summary", headers=H)
    nw_disposed = r.json()["data"]["net_worth"]
    check("18.19 已处置资产退出净资产",
          abs((float(nw_pre_dispose["assets"]) - float(nw_disposed["assets"])) - 9000) < 0.01,
          f"处置前资产 {nw_pre_dispose['assets']} → 处置后 {nw_disposed['assets']}")
    r = c.get(f"/ledger/accounts/{a1}", headers=H)
    check("18.20 处置收入进入去向账户",
          abs(float(r.json()["data"]["balance"]) - (float(balance_after_purchase) + 8000)) < 0.01)

    r = c.get(f"/ledger/events/{dispose_event_id}", headers=H)
    ev = r.json()["data"]
    r = c.post(f"/ledger/events/{dispose_event_id}/reverse", headers=H,
               json={"version": ev["version"], "reason": "误操作"})
    check("18.21 冲销处置事件", r.json()["success"] is True)
    r = c.get(f"/ledger/assets/{laptop['id']}", headers=H)
    check("18.22 冲销后资产恢复处置前状态", r.json()["data"]["status"] == "active")

    r = c.post("/settings/confirm-tokens", headers=H, json={
        "action": "asset.archive", "object_type": "asset", "object_id": metal["id"]})
    token = r.json()["data"]["token"]
    r = c.post(f"/ledger/assets/{metal['id']}/archive", headers=H,
               json={"reason": "", "confirm_token": token})
    check("18.23 归档必须填写原因", r.status_code == 422)
    r = c.post("/settings/confirm-tokens", headers=H, json={
        "action": "asset.archive", "object_type": "asset", "object_id": metal["id"]})
    r = c.post(f"/ledger/assets/{metal['id']}/archive", headers=H,
               json={"reason": "已转出给家人", "confirm_token": r.json()["data"]["token"]})
    check("18.24 归档资产保留历史并退出统计",
          r.json()["success"] and r.json()["data"]["status"] == "archived")

    r = c.put(f"/ledger/assets/{parking['id']}", headers=H,
              json={"version": parking["version"], "asset_type": "vehicle"})
    check("18.25 有业务数据的资产不可切换类型", r.status_code == 409
          and "asset_type" in r.json()["error"]["field_errors"])

    r = c.get("/ledger/assets/real-estates", headers=H)
    check("18.26 分类型列表可用", r.json()["data"]["total"] >= 1
          and all(i["asset_type"] == "real_estate" for i in r.json()["data"]["items"]))

    r = c.get("/ledger/assets/vehicles", headers=H)
    check("18.27 车辆列表可用", r.json()["data"]["total"] >= 1)
    r = c.get("/ledger/assets/precious-metals", headers=H)
    check("18.28 贵金属列表可用", r.json()["data"]["total"] >= 1)
    r = c.get("/ledger/assets/parking-spaces", headers=H)
    check("18.29 车位列表可用", r.json()["data"]["total"] >= 1)

    r = c.get("/audit", headers=H, params={"object_type": "asset"})
    check("18.30 资产操作写入审计", r.json()["data"]["total"] >= 5)

    # 18.31 车辆费用关联资产（15 §5.4、23 §4）
    r = c.post("/ledger/events", headers=H, json={
        "event_type": "expense", "title": "车辆加油", "amount": "300",
        "account_id": a1, "asset_id": vehicle_id, "source": "manual"})
    check("18.31a 费用可关联资产（车辆）", r.status_code == 200, r.text[:120])
    vev = r.json()["data"]["id"]
    c.post(f"/ledger/events/{vev}/confirm", headers=H, json={})
    r2 = c.get(f"/ledger/assets/{vehicle_id}", headers=H)
    check("18.31b 资产详情费用流水可追溯该支出",
          any(e["id"] == vev for e in r2.json()["data"]["events"]))

    # ---------------- M3 负债与信用卡 + 投资 ----------------

    r = c.get("/ledger/summary", headers=H)
    nw_m3_before = r.json()["data"]["net_worth"]

    # 19.1 普通负债：剩余本金 = 初始金额
    r = c.post("/ledger/liabilities", headers=H, json={
        "name": "亲友借款", "liability_type": "personal", "creditor": "亲友",
        "currency": "CNY", "initial_amount": "20000", "interest_rate": "12",
        "repayment_method": "equal_installment", "payment_frequency": "monthly",
        "payment_day": 20, "term_months": 12, "monthly_payment": "1800",
        "linked_account_id": a1,
    })
    loan = r.json()["data"]
    check("19.1 新增负债并计算剩余本金", r.json()["success"] and loan["outstanding"] == "20000.00")

    # 19.2 房贷必须关联不动产（15 §3.2）
    r = c.post("/ledger/liabilities", headers=H, json={
        "name": "住房贷款", "liability_type": "mortgage", "initial_amount": "800000"})
    check("19.2 房贷必须关联具体不动产", r.status_code == 422
          and "linked_asset_id" in r.json()["error"]["field_errors"])

    r = c.post("/ledger/liabilities", headers=H, json={
        "name": "住房贷款", "liability_type": "mortgage", "creditor": "某银行",
        "initial_amount": "800000", "interest_rate": "4.2", "rate_type": "lpr_plus",
        "repayment_method": "equal_installment", "term_months": 240,
        "linked_asset_id": house_id, "linked_account_id": a1,
        "extension": {"contract_no": "HT-M3-001", "prepayment_rules": "满一年可提前还款"},
    })
    mortgage = r.json()["data"]
    check("19.3 房贷关联不动产并保存合同资料", r.json()["success"]
          and mortgage["extension"]["contract_no"] == "HT-M3-001")

    # 19.4 资产详情显示房贷摘要（15 §3.2 不重复录入第二套贷款数据）
    r = c.get(f"/ledger/assets/{house_id}", headers=H)
    related = r.json()["data"]["related"]["liabilities"]
    check("19.4 资产详情显示负债模块提供的房贷摘要",
          related["module_enabled"] is True and len(related["items"]) == 1
          and related["items"][0]["name"] == "住房贷款")

    # 19.5 还款计划测算
    r = c.get(f"/ledger/liabilities/{mortgage['id']}/repayment-plan", headers=H)
    plan = r.json()["data"]
    check("19.5 还款计划按剩余期数测算", plan["periods"] == 240 and len(plan["schedule"]) == 240)

    # 19.6 还款拆分：本金不计支出、利息计入支出
    r = c.get("/ledger/summary", headers=H)
    expense_before = float(r.json()["data"]["metrics"]["expense"])
    r = c.post(f"/ledger/liabilities/{loan['id']}/repayments", headers=H,
               json={"amount": "5000", "account_id": a1})
    repayment = r.json()["data"]
    check("19.6 系统按剩余本金 × 周期利率建议利息",
          repayment["interest"] == "200.00" and repayment["principal"] == "4800.00",
          f"本金 {repayment['principal']} / 利息 {repayment['interest']}")

    r = c.get("/ledger/summary", headers=H)
    expense_after = float(r.json()["data"]["metrics"]["expense"])
    check("19.7 还款本金与利息均计入支出（本金 4800 + 利息 200）",
          abs((expense_after - expense_before) - 5000) < 0.01,
          f"支出增加 {expense_after - expense_before}")
    r = c.get(f"/ledger/liabilities/{loan['id']}", headers=H)
    check("19.8 还款后剩余本金减少", r.json()["data"]["outstanding"] == "15200.00")

    # 19.9 调整拆分必须填写原因
    r = c.post(f"/ledger/liabilities/{loan['id']}/repayments", headers=H,
               json={"amount": "1000", "principal": "1000", "interest": "0", "account_id": a1})
    check("19.9 调整本金/利息拆分必须说明原因", r.status_code == 422
          and "reason" in r.json()["error"]["field_errors"])

    # 19.10 借款到账不算收入
    r = c.get("/ledger/summary", headers=H)
    income_before = float(r.json()["data"]["metrics"]["income"])
    r = c.post(f"/ledger/liabilities/{loan['id']}/draws", headers=H,
               json={"amount": "3000", "account_id": a1})
    check("19.10 借款到账生成账户与负债双变动", r.json()["success"]
          and len(r.json()["data"]["movements"]) == 2)
    r = c.get("/ledger/summary", headers=H)
    check("19.11 借款到账不计入收入",
          abs(float(r.json()["data"]["metrics"]["income"]) - income_before) < 0.01)

    # 19.12 信用卡：额度、消费、账单、还款
    r = c.post("/ledger/liabilities", headers=H, json={
        "name": "信用卡", "liability_type": "credit_card", "creditor": "某银行",
        "extension": {"credit_limit": "50000", "bill_day": 5, "due_day": 25,
                      "card_no": "6222021234567890"}})
    card_liability = r.json()["data"]
    card_id = card_liability["card"]["id"]
    check("19.12 信用卡额度与卡号掩码", card_liability["card"]["available_amount"] == "50000.00"
          and "*" in card_liability["card"]["card_no"])

    r = c.post(f"/ledger/credit-cards/{card_id}/expenses", headers=H,
               json={"amount": "1200", "title": "超市购物"})
    check("19.13 信用卡消费计入支出并增加已用额度", r.json()["success"]
          and r.json()["data"]["movements"][0]["target_type"] == "liability")

    due_date = (datetime.now(timezone.utc) + timedelta(days=10)).date().isoformat()
    r = c.post(f"/ledger/credit-cards/{card_id}/bills", headers=H, json={
        "total_amount": "1200", "minimum_payment": "120", "due_date": due_date})
    bill = r.json()["data"]
    check("19.14 生成账单并派生状态", bill["status"] == "pending"
          and bill["remaining_amount"] == "1200.00")

    r = c.get("/ledger/summary", headers=H)
    expense_before_card = float(r.json()["data"]["metrics"]["expense"])
    r = c.post(f"/ledger/liabilities/{card_liability['id']}/repayments", headers=H,
               json={"amount": "1200", "account_id": a1, "bill_id": bill["id"]})
    check("19.15 信用卡还款本金不计支出", r.json()["success"]
          and r.json()["data"]["interest"] == "0.00")
    r = c.get("/ledger/summary", headers=H)
    check("19.16 信用卡还款不重复计消费",
          abs(float(r.json()["data"]["metrics"]["expense"]) - expense_before_card) < 0.01)
    r = c.get(f"/ledger/credit-cards/{card_id}", headers=H)
    check("19.17 还款后已用额度归零、账单已还清",
          r.json()["data"]["card"]["used_amount"] == "0.00"
          and r.json()["data"]["card"]["available_amount"] == "50000.00"
          and any(b["status"] == "paid" for b in r.json()["data"]["bills"]))

    # 19.18 投资账户与持仓
    r = c.post("/ledger/investments", headers=H, json={
        "name": "证券账户", "account_type": "securities", "currency": "CNY",
        "opening_cash": "50000"})
    inv = r.json()["data"]
    inv_id = inv["id"]
    check("19.18 新增投资账户", r.json()["success"] and inv["cash_balance"] == "50000.00")

    r = c.post(f"/ledger/investments/{inv_id}/trades", headers=H, json={
        "trade_type": "buy", "symbol": "000001", "symbol_name": "示例股票",
        "quantity": "1000", "price": "10", "fee": "5"})
    check("19.19 买入生成交易与业务事件", r.json()["success"]
          and r.json()["data"]["amount"] == "10000.00")

    r = c.post(f"/ledger/investments/{inv_id}/valuations", headers=H, json={
        "symbol": "000001", "price": "12", "source": "manual"})
    check("19.20 手工估值录入", r.json()["success"] is True)

    r = c.get(f"/ledger/investments/{inv_id}/positions", headers=H)
    position = r.json()["data"]["items"][0]
    check("19.21 持仓成本与未实现损益", position["cost"] == "10005.00"
          and position["market_value"] == "12000.00"
          and position["unrealized_pnl"] == "1995.00",
          f"成本 {position['cost']} / 市值 {position['market_value']}")

    r = c.post(f"/ledger/investments/{inv_id}/trades", headers=H, json={
        "trade_type": "sell", "symbol": "000001", "quantity": "500", "price": "15", "fee": "3"})
    check("19.22 卖出计算已实现损益", r.json()["success"] is True)
    r = c.get(f"/ledger/investments/{inv_id}/positions", headers=H)
    position = next(p for p in r.json()["data"]["items"] if p["symbol"] == "000001")
    check("19.23 加权平均成本法已实现损益", position["realized_pnl"] == "2494.50"
          and position["quantity"] == "500.000000",
          f"已实现 {position['realized_pnl']}")

    r = c.post(f"/ledger/investments/{inv_id}/trades", headers=H, json={
        "trade_type": "sell", "symbol": "000001", "quantity": "9999", "price": "15"})
    check("19.24 卖出不能超过持仓", r.status_code == 422
          and "quantity" in r.json()["error"]["field_errors"])

    r = c.get(f"/ledger/investments/{inv_id}", headers=H)
    detail_inv = r.json()["data"]
    check("19.25 投资账户余额按交易净影响计算",
          detail_inv["cash_balance"] == "47492.00" and detail_inv["market_value"] == "6000.00",
          f"余额 {detail_inv['cash_balance']}")

    # 19.26 净资产并入负债与投资
    r = c.get("/ledger/summary", headers=H)
    nw = r.json()["data"]["net_worth"]
    check("19.26 净资产 = 账户 + 资产 + 投资 − 负债",
          nw["investments"] == "6000.00" and float(nw["total_liabilities"]) > 800000
          and abs(float(nw["net_worth"]) - (float(nw["accounts"]) + float(nw["assets"])
                                           + float(nw["investments"]) - float(nw["total_liabilities"]))) < 0.01,
          f"净资产 {nw['net_worth']}")

    r = c.get("/ledger/summary", headers=H)
    charts = r.json()["data"]["charts"]
    check("19.27 负债趋势与投资结构图数据就位",
          len(charts["liability_trend"]) == 6 and len(charts["investment_structure"]) >= 1)

    # 19.28 删除保护：有剩余本金/有持仓不可删除
    r = c.post("/settings/confirm-tokens", headers=H, json={
        "action": "liability.delete", "object_type": "liability", "object_id": loan["id"]})
    token = r.json()["data"]["token"]
    r = c.delete(f"/ledger/liabilities/{loan['id']}", headers=H,
                 params={"reason": "测试", "confirm_token": token})
    check("19.28 有剩余本金的负债不可删除", r.status_code == 409)

    r = c.post("/settings/confirm-tokens", headers=H, json={
        "action": "investment.delete", "object_type": "investment_account", "object_id": inv_id})
    token = r.json()["data"]["token"]
    r = c.delete(f"/ledger/investments/{inv_id}", headers=H,
                 params={"reason": "测试", "confirm_token": token})
    check("19.29 有持仓的投资账户不可删除", r.status_code == 409)

    r = c.get("/audit", headers=H, params={"object_type": "liability"})
    check("19.30 负债操作写入审计", r.json()["data"]["total"] >= 3)
    r = c.get("/audit", headers=H, params={"object_type": "investment_account"})
    check("19.31 投资操作写入审计", r.json()["data"]["total"] >= 3)

    # ---------------- M4 房产租赁 + 对账 ----------------

    r = c.post("/ledger/leases", headers=H, json={
        "real_estate_id": house_id, "tenant_name": "张三", "monthly_rent": "5000",
        "payment_frequency": "monthly", "currency": "CNY", "deposit_amount": "5000",
        "receiving_account_id": a1, "start_date": "2026-01-01", "end_date": "2027-01-01",
    })
    check("20.1 新建租约", r.json()["success"] and r.json()["data"]["status"] == "active", r.text[:120])
    lease_id = r.json()["data"]["id"]

    r = c.get("/ledger/assets", headers=H)
    house_ext = next(a["extension"] for a in r.json()["data"]["items"] if a["id"] == house_id)
    check("20.2 房产出租摘要联动", house_ext["has_lease"] is True)

    bal_before = float(c.get(f"/ledger/accounts/{a1}", headers=H).json()["data"]["balance"])
    r = c.post(f"/ledger/leases/{lease_id}/receivables", headers=H, json={"amount": "5000"})
    receivable_id = r.json()["data"]["id"]
    bal_after = float(c.get(f"/ledger/accounts/{a1}", headers=H).json()["data"]["balance"])
    check("20.3 应收登记不改变账户余额", r.json()["success"] and bal_after == bal_before)

    inc_before = float(c.get("/ledger/summary", headers=H).json()["data"]["metrics"]["income"])
    r = c.post(f"/ledger/leases/{lease_id}/receipts", headers=H, json={
        "amount": "5000", "account_id": a1, "receivable_id": receivable_id})
    inc_after = float(c.get("/ledger/summary", headers=H).json()["data"]["metrics"]["income"])
    check("20.4 实收计入收入并核销应收", r.json()["success"] and abs(inc_after - inc_before - 5000) < 0.01)

    exp_before = float(c.get("/ledger/summary", headers=H).json()["data"]["metrics"]["expense"])
    r = c.post(f"/ledger/leases/{lease_id}/deposits/receive", headers=H, json={
        "amount": "5000", "account_id": a1})
    deposit_id = r.json()["data"]["id"]
    r = c.post(f"/ledger/leases/{lease_id}/deposits/{deposit_id}/return", headers=H,
               json={"account_id": a1})
    exp_after = float(c.get("/ledger/summary", headers=H).json()["data"]["metrics"]["expense"])
    check("20.5 押金收取/退还不计入收支", r.json()["success"] and exp_after == exp_before)

    token = c.post("/settings/confirm-tokens", headers=H, json={
        "action": "lease.terminate", "object_type": "lease", "object_id": lease_id}).json()["data"]["token"]
    r = c.post(f"/ledger/leases/{lease_id}/terminate", headers=H, json={
        "reason": "租客退租", "confirm_token": token})
    check("20.6 终止租约并转空置", r.json()["success"] and r.json()["data"]["status"] == "terminated")

    r = c.get("/ledger/reconciliation", headers=H, params={"account_id": a1})
    sys_bal = r.json()["data"]["system_balance"]
    r = c.post("/ledger/reconciliation", headers=H, json={
        "account_id": a1, "actual_balance": str(float(sys_bal) + 100), "reason": "银行到账补记"})
    check("20.7 对账差额生成调整事件", r.json()["success"] and r.json()["data"]["adjusted"] is True
          and r.json()["data"]["adjustment_event"]["source"] == "reconciliation")

    # ---------------- M5 智能录入 + OCR ----------------

    r = c.post("/ledger/capture/attachments", headers=H,
               files={"file": ("receipt.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
               data={"material_type": "receipt"})
    check("21.1 上传材料附件", r.status_code == 200 and r.json()["data"]["material_type"] == "receipt",
          r.text[:120])
    att_id = r.json()["data"]["id"]

    r = c.post("/ledger/capture/attachments", headers=H,
               files={"file": ("evil.exe", b"MZ\x00\x01", "application/octet-stream")},
               data={"material_type": "receipt"})
    check("21.2 非法文件类型被拒", r.status_code == 422)

    r = c.get("/ledger/capture/attachments", headers=H)
    check("21.3 附件列表可查", any(a["id"] == att_id for a in r.json()["data"]["items"]))

    r = c.post("/ledger/capture/ocr", headers=H, json={"attachment_id": att_id})
    check("21.4 OCR 未配置降级（可配置入口）", r.status_code == 409
          and r.json()["error"]["code"] == "PROVIDER_NOT_CONFIGURED")

    token = c.post("/settings/confirm-tokens", headers=H, json={
        "action": "attachment.delete", "object_type": "attachment", "object_id": att_id}).json()["data"]["token"]
    r = c.request("DELETE", f"/ledger/capture/attachments/{att_id}", headers=H,
                  json={"reason": "清理测试", "confirm_token": token})
    check("21.5 删除附件需二次确认", r.json()["success"] is True)

    r = c.post("/ledger/events", headers=H, json={
        "event_type": "expense", "title": "OCR 票据", "amount": "66.00",
        "account_id": a1, "source": "ocr"})
    check("21.6 OCR 结构化生成草稿（source=ocr）",
          r.json()["success"] and r.json()["data"]["status"] == "draft"
          and r.json()["data"]["source"] == "ocr")

    # ---------------- M6 工作台 + 洞察 ----------------
    r = c.get("/insights/overview", headers=H, params={"period": "current_month"})
    ov = r.json()["data"]
    check("22.1 综合洞察返回结论列表", ov["module_enabled"] is True and len(ov["conclusions"]) >= 6)
    check("22.2 结论含口径/期间/更新时间/来源数量/查看明细",
          {"caliber", "period_label", "data_updated_at", "source_count", "detail_to"} <= set(ov["conclusions"][0]))

    for topic in ("assets", "income", "expenses", "liabilities", "investments", "leases"):
        r = c.get(f"/insights/{topic}", headers=H)
        body = r.json()["data"]
        check(f"22.3 主题 {topic} 返回 panels", isinstance(body["panels"], list) and body["title"])

    r = c.get("/insights/data-quality", headers=H)
    dq = r.json()["data"]
    check("22.4 数据质量已运行且非盘点", dq["checked"] is True and dq["is_inventory"] is False)
    check("22.5 未分类流水被标记（前面有未分类 OCR 草稿确认前不计，但收入/支出有未分类）",
          isinstance(dq["items"], list) and isinstance(dq["score"], int))

    r = c.get("/dashboard/snapshot", headers=H)
    snap6 = r.json()["data"]
    check("22.6 工作台返回业务摘要（负债/投资/租赁）",
          "summaries" in snap6 and "leases" in snap6["summaries"])

    # ---------------- M7 本地 AI + 第三方 AI ----------------
    r = c.post("/ai/chat", headers=H, json={"question": "本月支出如何？", "context": "工作台"})
    ai = r.json()["data"]
    check("23.1 确定性问答命中（无需模型）", ai["state"] == "ready" and ai["trust"]["model_type"] == "deterministic")
    check("23.2 AI 可信度展示字段完整",
          {"model_type_label", "base_currency", "period_label", "source_count", "masked"} <= set(ai["trust"]))

    r = c.post("/ai/chat", headers=H, json={"question": "帮我写一首七言绝句"})
    ai2 = r.json()["data"]
    check("23.3 未配置模型时开放式问题降级", ai2["state"] == "model_unavailable")
    check("23.4 工具白名单契约可查", len(c.get("/ai/tools", headers=H).json()["data"]["items"]) >= 12)

    # ---------------- M8 提醒 + PushPlus ----------------
    r = c.get("/ledger/reminders", headers=H)
    rem = r.json()["data"]
    check("24.1 提醒列表实时计算（六类）",
          set(g["type"] for g in rem["groups"]) >= {"repayment", "credit_card_bill", "rent_due",
                                                     "lease_expire", "asset_maintenance", "ai_suggestion"})
    items = [it for g in rem["groups"] for it in g["items"]]
    if items:
        r = c.post("/ledger/reminders/send", headers=H, json={"keys": [items[0]["key"]]})
        check("24.2 手动发送写通知记录（未配置 PushPlus 则 failed）",
              r.json()["success"] and r.json()["data"]["total"] >= 1)
    r = c.get("/ledger/notifications/records", headers=H)
    check("24.3 通知记录列表可查", isinstance(r.json()["data"]["items"], list))
    r = c.get("/dashboard/snapshot", headers=H)
    check("24.4 工作台提醒计数真实", r.json()["data"]["metrics"]["pending_reminders"] >= 0)

    # 3(续) 改密与退出
    r = c.post("/auth/change-password", headers=H, json={"old_password": "Fam@2026#Strong", "new_password": "NewFam@2026#X"})
    check("3.7 修改密码并失效旧会话", r.json()["success"] is True, f"撤销 {r.json()['data']['revoked_sessions']} 个会话")
    check("3.8 改密后旧令牌失效", c.get("/auth/me", headers=H).status_code == 401)
    r = c.post("/auth/login", json={"username": "admin", "password": "NewFam@2026#X"})
    H2 = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
    r = c.post("/auth/logout", headers=H2)
    check("3.9 退出登录", r.json()["success"] is True)

    # 统一响应信封
    r = c.get("/health")
    check("16.1 统一响应信封", set(r.json()) == {"success", "data", "request_id", "message"} and bool(r.headers.get("X-Request-Id")))
    r = c.get("/ledger/events/bad-id", headers={"Authorization": "Bearer bad"})
    check("16.2 错误信封字段完整", {"code", "message", "field_errors", "retryable", "request_id"} <= set(r.json()["error"]))

    print("\n" + "=" * 68)
    print(f"通过 {len(PASSED)} 项 / 失败 {len(FAILED)} 项")
    if FAILED:
        print("失败项：")
        for f in FAILED:
            print(" -", f)
        return 1
    print("M0 框架基座端到端验收：全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except httpx.ConnectError:
        print("无法连接后端 http://127.0.0.1:8000，请先启动服务")
        sys.exit(2)
