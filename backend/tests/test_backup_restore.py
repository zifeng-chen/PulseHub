"""备份与离线恢复的闭环验证。

覆盖：创建备份 → 备份后改动数据 → 离线恢复 → 数据回滚到备份时刻。
这条链路此前是空的：备份只存了附件，恢复只写了一条待执行记录。
"""

from __future__ import annotations

from tests.conftest import ADMIN, auth, complete_setup


async def test_backup_and_restore_roundtrip(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    # 1) 创建备份（需要二次确认令牌）
    tok = (await client.post(
        "/api/v1/settings/confirm-tokens", headers=h,
        json={"action": "backup.create", "object_type": "backup", "object_id": None},
    )).json()["data"]["token"]
    created = await client.post("/api/v1/settings/backup/create", headers=h,
                                json={"confirm_token": tok})
    assert created.json()["success"] is True, created.text
    backup_no = created.json()["data"]["backup_no"]

    before = (await client.get("/api/v1/ledger/accounts", headers=h)).json()["data"]

    # 2) 备份之后新增一个账户，制造"需要回滚"的差异
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "备份后新增账户", "account_type": "cash", "currency": "CNY", "opening_balance": "0",
    })
    assert r.json()["success"] is True, r.text
    after_create = (await client.get("/api/v1/ledger/accounts", headers=h)).json()["data"]
    assert after_create["total"] == before["total"] + 1

    # 3) 离线恢复（独立 engine，整库覆盖）
    from app.services import backup as backup_service
    result = await backup_service.apply_restore(backup_no)

    assert result["status"] == "success"
    assert result["restored_tables"] > 10, result
    assert result["restored_rows"] > 0, result
    assert result["safety_snapshot"].startswith("SAFE")

    # 4) 恢复后会话被清空：旧 token 必须失效，重新登录后才能访问
    stale = await client.get("/api/v1/ledger/accounts", headers=h)
    assert stale.status_code == 401, stale.text

    relogin = (await client.post("/api/v1/auth/login", json={
        "username": ADMIN["username"], "password": ADMIN["password"],
    })).json()
    assert relogin["success"] is True, relogin
    h2 = auth(relogin["data"]["access_token"])

    restored = (await client.get("/api/v1/ledger/accounts", headers=h2)).json()["data"]
    assert restored["total"] == before["total"], f"恢复前 {before['total']}，恢复后 {restored['total']}"
    assert all(a["name"] != "备份后新增账户" for a in restored["items"])

    # 5) 备份清单没有被恢复回滚掉
    records = (await client.get("/api/v1/settings/backup", headers=h2)).json()["data"]
    assert records["items"], "恢复后备份清单不应为空"
    assert records["items"][0]["backup_no"] == backup_no


async def test_restore_rejects_tampered_backup(client, tmp_path):
    """备份目录被改动后必须拒绝恢复（校验和不一致）。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    tok = (await client.post(
        "/api/v1/settings/confirm-tokens", headers=h,
        json={"action": "backup.create", "object_type": "backup", "object_id": None},
    )).json()["data"]["token"]
    created = await client.post("/api/v1/settings/backup/create", headers=h,
                                json={"confirm_token": tok})
    backup_no = created.json()["data"]["backup_no"]

    # 篡改备份目录
    import os

    from app.core.config import settings
    from app.services import backup as backup_service

    target = os.path.join(settings.backup_dir, backup_no, "tampered.txt")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("evil")

    import pytest

    from app.core.errors import AppError

    with pytest.raises(AppError) as exc:
        await backup_service.apply_restore(backup_no)
    assert "校验失败" in str(exc.value)
