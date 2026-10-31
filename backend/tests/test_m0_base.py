"""M0 框架基座验收测试（12 §2、18 §3、21 §5）。"""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN, auth, complete_setup


# ---------------- 安装检测与首次启动 ----------------

async def test_install_check_and_setup_status(client):
    r = await client.get("/api/v1/setup/status")
    body = r.json()
    assert body["success"] is True
    assert body["data"]["initialized"] is False
    assert body["data"]["next"] == "setup"

    r = await client.get("/api/v1/setup/check")
    data = r.json()["data"]
    assert r.status_code == 200
    assert data["overall"] in ("pass", "fail")
    assert {c["key"] for c in data["checks"]} >= {"backend", "database", "file", "migration"}


async def test_setup_wizard_full_flow(client):
    tokens = await complete_setup(client)
    assert tokens["user"]["is_system_admin"] is True
    assert tokens["user"]["role"] in ("owner", "admin")
    assert "configure" in tokens["user"]["actions"]

    # 初始化完成之后，摘要对匿名请求只给状态、不给身份信息（公开页不能泄露管理员与家庭名）
    r = await client.get("/api/v1/setup/summary")
    summary = r.json()["data"]
    assert summary["initialized"] is True
    assert summary["household"] is None
    assert summary["providers"]["local_ai"] == "unconfigured"

    # 登录之后能看到完整摘要（设置页/完成页要用）
    r = await client.get("/api/v1/setup/summary", headers=auth(tokens["access"]))
    logged_in = r.json()["data"]
    assert logged_in["household"]["name"] == "测试家庭"
    assert logged_in["admin"]["username"] == ADMIN["username"]


async def test_setup_endpoints_closed_after_initialization(client):
    await complete_setup(client)
    r = await client.post("/api/v1/setup/admin", json=ADMIN)
    assert r.status_code == 409
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "SETUP_ALREADY_DONE"
    assert body["error"]["message"]  # 中文说明


async def test_setup_rejects_weak_password_and_duplicate(client):
    await client.post("/api/v1/setup/database", json={"use_embedded_dev_database": True})
    r = await client.post("/api/v1/setup/admin", json={**ADMIN, "password": "123456",
                                                       "confirm_password": "123456"})
    assert r.status_code == 422
    assert "password" in r.json()["error"]["field_errors"]

    r = await client.post("/api/v1/setup/admin", json={**ADMIN, "confirm_password": "different"})
    assert r.status_code == 422
    assert "confirm_password" in r.json()["error"]["field_errors"]


# ---------------- 登录与会话 ----------------

async def test_login_failure_does_not_reveal_account_existence(client):
    await complete_setup(client)
    r1 = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-pass"})
    r2 = await client.post("/api/v1/auth/login", json={"username": "no-such-user", "password": "wrong-pass"})
    assert r1.status_code == 401 and r2.status_code == 401
    assert r1.json()["error"]["message"] == r2.json()["error"]["message"]


async def test_account_lock_after_repeated_failures(client):
    await complete_setup(client)
    for _ in range(3):
        await client.post("/api/v1/auth/login", json={"username": "admin", "password": "bad"})
    r = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "bad"})
    assert r.status_code == 423
    assert r.json()["error"]["code"] == "ACCOUNT_LOCKED"


async def test_session_refresh_rotate_and_revoke(client):
    tokens = await complete_setup(client)
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh"]})
    assert r.json()["success"] is True
    new_refresh = r.json()["data"]["refresh_token"]
    assert new_refresh != tokens["refresh"]

    # 旧刷新令牌轮换后失效
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh"]})
    assert r.status_code == 401

    r = await client.get("/api/v1/auth/sessions", headers=auth(tokens["access"]))
    items = r.json()["data"]["items"]
    assert items and items[0]["current"] is True

    r = await client.delete(f"/api/v1/auth/sessions/{tokens['session_id']}", headers=auth(tokens["access"]))
    assert r.json()["success"] is True
    r = await client.get("/api/v1/auth/me", headers=auth(tokens["access"]))
    assert r.status_code == 401


async def test_logout_and_unauthorized_access(client):
    tokens = await complete_setup(client)
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    r = await client.post("/api/v1/auth/logout", headers=auth(tokens["access"]))
    assert r.json()["success"] is True
    assert (await client.get("/api/v1/auth/me", headers=auth(tokens["access"]))).status_code == 401


async def test_change_password(client):
    tokens = await complete_setup(client)
    r = await client.post("/api/v1/auth/change-password", headers=auth(tokens["access"]),
                          json={"old_password": ADMIN["password"], "new_password": "NewFam@2026#X"})
    assert r.json()["success"] is True
    r = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "NewFam@2026#X"})
    assert r.json()["success"] is True


# ---------------- 家庭、成员与权限 ----------------

async def test_create_member_and_readonly_permission(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    r = await client.post("/api/v1/settings/members", headers=h,
                          json={"username": "viewer", "display_name": "只读成员",
                                "password": "Viewer@2026#1", "role": "readonly"})
    assert r.json()["success"] is True

    r = await client.post("/api/v1/auth/login", json={"username": "viewer", "password": "Viewer@2026#1"})
    viewer = r.json()["data"]
    assert viewer["user"]["role"] == "readonly"
    vh = auth(viewer["access_token"])

    # 只读成员可以查看
    assert (await client.get("/api/v1/dashboard/snapshot", headers=vh)).status_code == 200
    # 只读成员不能创建草稿
    r = await client.post("/api/v1/ledger/events", headers=vh,
                          json={"event_type": "expense", "title": "不应成功", "amount": "10"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
    # 只读成员不能查看审计
    assert (await client.get("/api/v1/audit", headers=vh)).status_code == 403


# ---------------- 业务事件：草稿 / 确认 / 冲销 ----------------

async def test_event_draft_confirm_reverse_with_audit(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    r = await client.post("/api/v1/ledger/events", headers=h,
                          json={"event_type": "expense", "title": "超市采购", "amount": "128.50",
                                "currency": "CNY", "counterparty": "某某超市"})
    event = r.json()["data"]
    assert event["status"] == "draft"
    assert event["counts_in_stats"] is False
    assert event["amount"] == "128.50"

    # 草稿未确认时对账数据仍为空
    r = await client.post(f"/api/v1/ledger/events/{event['id']}/confirm", headers=h,
                          json={"version": event["version"]})
    confirmed = r.json()["data"]
    assert confirmed["status"] == "confirmed"
    assert confirmed["counts_in_stats"] is True
    assert len(confirmed["movements"]) == 1

    # 版本冲突 409
    r = await client.post(f"/api/v1/ledger/events/{event['id']}/confirm", headers=h,
                          json={"version": event["version"]})
    assert r.status_code == 409

    # 冲销必须填写原因
    r = await client.post(f"/api/v1/ledger/events/{event['id']}/reverse", headers=h,
                          json={"reason": ""})
    assert r.status_code == 422
    r = await client.post(f"/api/v1/ledger/events/{event['id']}/reverse", headers=h,
                          json={"reason": "录错金额", "version": confirmed["version"]})
    assert r.json()["data"]["status"] == "reversed"

    # 审计记录可查且含字段级差异与中文标签
    r = await client.get("/api/v1/audit", headers=h)
    items = r.json()["data"]["items"]
    assert r.json()["data"]["total"] >= 3
    detail_id = next(i["id"] for i in items if i["object_type"] == "business_event")
    r = await client.get(f"/api/v1/audit/{detail_id}", headers=h)
    detail = r.json()["data"]
    assert detail["role_label"] and detail["source_method_label"]
    assert any(c["field_label"] for c in detail["changes"])


async def test_invalid_event_type_and_negative_amount(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    r = await client.post("/api/v1/ledger/events", headers=h,
                          json={"event_type": "unknown_type", "title": "x", "amount": "1"})
    assert r.status_code == 422
    assert "event_type" in r.json()["error"]["field_errors"]

    r = await client.post("/api/v1/ledger/events", headers=h,
                          json={"event_type": "expense", "title": "x", "amount": "-5"})
    assert r.status_code == 422
    assert "amount" in r.json()["error"]["field_errors"]


async def test_household_boundary_blocks_other_household_event(client):
    """越权读取：构造不存在于当前家庭的事件 ID 必须 404。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    r = await client.get("/api/v1/ledger/events/does-not-exist", headers=h)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


# ---------------- 统计口径与模块占位 ----------------

async def test_dashboard_snapshot_caliber_and_pending_modules(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    r = await client.get("/api/v1/dashboard/snapshot", headers=h)
    data = r.json()["data"]
    assert data["period_label"]
    assert data["caliber"]["excludes_draft"] is True
    assert "净资产" in data["caliber"]["net_worth"] or "余额" in data["caliber"]["net_worth"]

    # M2 起资产模块已交付：资产列表返回真实列表与统计，不再是模块占位
    r = await client.get("/api/v1/ledger/assets", headers=h)
    assets = r.json()["data"]
    assert assets["items"] == []
    assert assets["total"] == 0
    assert "summary" in assets and assets["summary"]["count"] == 0

    # M4 起房产租赁已交付：租约列表返回真实列表（空），不再是模块占位
    r = await client.get("/api/v1/ledger/leases", headers=h)
    leases = r.json()["data"]
    assert leases["items"] == []
    assert leases["total"] == 0

    # M5 起智能录入已交付：附件列表返回真实列表（空），不再是模块占位
    r = await client.get("/api/v1/ledger/capture/attachments", headers=h)
    capture = r.json()["data"]
    assert capture["items"] == []
    assert capture["total"] == 0

    # M8 起提醒已交付：返回六类提醒真实结构（空），不再是模块占位
    r = await client.get("/api/v1/ledger/reminders", headers=h)
    reminders = r.json()["data"]
    assert set(g["type"] for g in reminders["groups"]) == {
        "repayment", "credit_card_bill", "rent_due", "lease_expire",
        "asset_maintenance", "ai_suggestion",
    }
    assert reminders["total"] == 0


# ---------------- 能力适配器：未配置不阻塞 ----------------

async def test_unconfigured_capabilities_do_not_block_and_return_actionable_error(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    r = await client.get("/api/v1/ai/providers", headers=h)
    items = {i["key"]: i for i in r.json()["data"]["items"]}
    assert set(items) == {"local_ai", "third_party_ai", "ocr", "notification"}
    assert all(i["status"] == "unconfigured" for i in items.values())
    assert items["local_ai"]["status_label"] == "未配置"

    # 记账与统计仍然正常
    assert (await client.get("/api/v1/ledger/categories", headers=h)).status_code == 200

    r = await client.post("/api/v1/ai/providers/local_ai/test", headers=h)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "PROVIDER_NOT_CONFIGURED"
    assert "设置" in r.json()["error"]["message"]

    r = await client.post("/api/v1/ai/chat", headers=h, json={"question": "净资产是多少"})
    # M7 起财务数据类问题无需模型，用确定性统计直接回答
    assert r.json()["data"]["state"] == "ready"
    assert r.json()["data"]["trust"]["model_type"] == "deterministic"

    # 非财务开放式问题在模型未配置时降级
    r = await client.post("/api/v1/ai/chat", headers=h, json={"question": "帮我写一首诗"})
    assert r.json()["data"]["state"] == "model_unavailable"


# ---------------- 隐私与高影响动作二次确认 ----------------

async def test_cloud_egress_requires_confirm_token(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    r = await client.put("/api/v1/settings/privacy", headers=h,
                         json={"cloud_egress_enabled": True})
    assert r.status_code == 422
    assert "confirm_token" in r.json()["error"]["field_errors"]

    r = await client.post("/api/v1/settings/confirm-tokens", headers=h,
                          json={"action": "privacy.cloud_egress", "object_type": "privacy"})
    token = r.json()["data"]["token"]
    policy = (await client.get("/api/v1/settings/privacy", headers=h)).json()["data"]
    r = await client.put("/api/v1/settings/privacy", headers=h,
                         json={"cloud_egress_enabled": True, "confirm_token": token})
    assert r.json()["success"] is True

    # 令牌一次性：重复使用被拒绝
    r = await client.put("/api/v1/settings/privacy", headers=h,
                         json={"cloud_egress_enabled": False, "confirm_token": token})
    assert r.status_code == 422


async def test_base_currency_change_is_high_impact(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    r = await client.put("/api/v1/settings/finance", headers=h, json={"base_currency": "USD"})
    assert r.status_code == 422
    assert "base_currency" in r.json()["error"]["field_errors"] or "confirm_token" in r.json()["error"]["field_errors"]


# ---------------- 备份与恢复 ----------------

async def test_backup_create_verify_and_restore_preview(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    r = await client.post("/api/v1/settings/confirm-tokens", headers=h,
                          json={"action": "backup.create", "object_type": "backup"})
    token = r.json()["data"]["token"]
    r = await client.post("/api/v1/settings/backup/create", headers=h, json={"confirm_token": token})
    record = r.json()["data"]
    assert record["status"] == "created"
    assert record["checksum"]

    r = await client.post(f"/api/v1/settings/backup/{record['id']}/verify", headers=h)
    assert r.json()["data"]["integrity"] is True

    r = await client.get(f"/api/v1/settings/backup/{record['id']}/restore-preview", headers=h)
    assert r.json()["data"]["confirm_phrase_required"] == "确认恢复"

    r = await client.post(f"/api/v1/settings/backup/{record['id']}/restore", headers=h,
                          json={"confirm_phrase": "错误短语"})
    assert r.status_code == 422
    r = await client.post(f"/api/v1/settings/backup/{record['id']}/restore", headers=h,
                          json={"confirm_phrase": "确认恢复"})
    assert r.json()["success"] is True


# ---------------- 维护模式 ----------------

async def test_maintenance_mode_blocks_writes_but_allows_reads(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    r = await client.post("/api/v1/system/maintenance/enter", headers=h, json={"reason": "迁移演练"})
    assert r.json()["data"]["active"] is True

    r = await client.post("/api/v1/ledger/events", headers=h,
                          json={"event_type": "expense", "title": "维护期间写入", "amount": "1"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "MAINTENANCE_MODE"
    assert (await client.get("/api/v1/dashboard/snapshot", headers=h)).status_code == 200

    r = await client.post("/api/v1/system/maintenance/exit", headers=h)
    assert r.json()["data"]["active"] is False
    r = await client.post("/api/v1/ledger/events", headers=h,
                          json={"event_type": "expense", "title": "维护后写入", "amount": "1"})
    assert r.json()["success"] is True


# ---------------- 统一响应信封与错误码 ----------------

async def test_password_reset_requires_matching_recovery_email(client):
    """线上重置必须校验恢复邮箱；未配置恢复邮箱时只能走服务器本机离线脚本。"""
    await complete_setup(client)

    r = await client.post("/api/v1/auth/reset-password", json={
        "username": ADMIN["username"],
        "recovery_email": "attacker@example.com",
        "new_password": "NewPass!2345",
    })
    assert r.status_code == 422

    from sqlalchemy import update

    from app.db.base import SessionLocal
    from app.db.models.identity import User

    async with SessionLocal() as db:
        await db.execute(
            update(User).where(User.username == ADMIN["username"]).values(recovery_email=None)
        )
        await db.commit()

    r = await client.post("/api/v1/auth/reset-password", json={
        "username": ADMIN["username"],
        "new_password": "NewPass!2345",
    })
    assert r.status_code == 409
    assert "reset_admin_password" in r.json()["error"]["message"]

    # 原密码仍然有效，说明重置没有生效
    r = await client.post("/api/v1/auth/login", json={
        "username": ADMIN["username"], "password": ADMIN["password"], "remember_device": True,
    })
    assert r.json()["success"] is True


async def test_unified_envelope_shape(client):
    await complete_setup(client)
    r = await client.get("/api/v1/health")
    body = r.json()
    assert set(body) == {"success", "data", "request_id", "message"}
    assert r.headers.get("X-Request-Id")

    r = await client.get("/api/v1/ledger/events/none", headers={"Authorization": "Bearer bad"})
    assert r.status_code == 401
    err = r.json()["error"]
    assert {"code", "message", "field_errors", "retryable", "request_id"} <= set(err)


async def test_enum_dictionary_is_chinese(client):
    r = await client.get("/api/v1/meta/enums")
    registry = r.json()["data"]["registry"]
    assert registry["event_status"]["draft"] == "草稿"
    assert registry["event_type"]["transfer"] == "内部转账"
    assert registry["permission_action"]["audit_view"] == "审计查看"
