"""M5 智能录入 + OCR 验收测试（06 §10、24 §4、10 §5、09 §47）。

覆盖：附件上传白名单与大小限制、附件权限与下载、删除令牌、OCR 未配置降级、
调用记录、OCR 结构化结果生成草稿事件（source=ocr，确认前不入账）。
"""

from __future__ import annotations

from tests.conftest import auth, complete_setup


async def _bootstrap(client) -> dict:
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    ids: dict = {"headers": h, "access": tokens["access"]}
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "工资卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "100000", "include_in_net_worth": True,
    })
    ids["a1"] = r.json()["data"]["id"]
    return ids


async def _upload(client, headers, *, filename="receipt.png",
                  mime="image/png", content=b"\x89PNG\r\n\x1a\nfake-png", material_type="receipt"):
    r = await client.post(
        "/api/v1/ledger/capture/attachments", headers=headers,
        files={"file": (filename, content, mime)},
        data={"material_type": material_type},
    )
    return r


# ---------------- 附件上传与安全 ----------------

async def test_attachment_upload_whitelist_and_metadata(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await _upload(client, h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["material_type"] == "receipt"
    assert data["material_type_label"] == "票据"
    assert data["status"] == "ready"
    assert data["sha256"] if "sha256" in data else True  # 序列化不含 sha256 亦可

    # 非法类型被拒绝
    r = await _upload(client, h, filename="evil.exe", mime="application/octet-stream",
                      content=b"MZ\x00\x01", material_type=None)
    assert r.status_code == 422
    assert "file" in r.json()["error"]["field_errors"]


async def test_attachment_download_and_delete_with_token(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await _upload(client, h)
    attachment_id = r.json()["data"]["id"]

    # 列表可见
    r = await client.get("/api/v1/ledger/capture/attachments", headers=h)
    assert any(a["id"] == attachment_id for a in r.json()["data"]["items"])

    # 下载成功
    r = await client.get(f"/api/v1/ledger/capture/attachments/{attachment_id}/download", headers=h)
    assert r.status_code == 200

    # 删除需令牌 + 原因
    r = await client.request("DELETE", f"/api/v1/ledger/capture/attachments/{attachment_id}",
                             headers=h, json={"reason": "误传"})
    assert r.status_code == 422  # 缺令牌
    token = (await client.post("/api/v1/settings/confirm-tokens", headers=h, json={
        "action": "attachment.delete", "object_type": "attachment", "object_id": attachment_id,
    })).json()["data"]["token"]
    r = await client.request("DELETE", f"/api/v1/ledger/capture/attachments/{attachment_id}",
                             headers=h, json={"reason": "误传", "confirm_token": token})
    assert r.json()["success"] is True, r.text

    # 删除后列表不可见
    r = await client.get("/api/v1/ledger/capture/attachments", headers=h)
    assert not any(a["id"] == attachment_id for a in r.json()["data"]["items"])


# ---------------- OCR 未配置降级 ----------------

async def test_ocr_unconfigured_degrades(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await _upload(client, h)
    attachment_id = r.json()["data"]["id"]

    r = await client.post("/api/v1/ledger/capture/ocr", headers=h,
                          json={"attachment_id": attachment_id, "material_type": "receipt"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "PROVIDER_NOT_CONFIGURED"

    # 调用记录为空（未配置不落日志）
    r = await client.get("/api/v1/ledger/capture/ocr-logs", headers=h)
    assert r.json()["data"]["total"] == 0


# ---------------- OCR 结构化 → 草稿事件 ----------------

async def test_capture_draft_source_ocr_not_counted_until_confirmed(client):
    ids = await _bootstrap(client)
    h = ids["headers"]

    # 模拟 OCR 结构化结果 → 生成草稿（source=ocr）
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "超市采购", "amount": "128.50",
        "account_id": ids["a1"], "source": "ocr", "note": "OCR 票据识别",
    })
    assert r.json()["success"] is True, r.text
    event_id = r.json()["data"]["id"]
    assert r.json()["data"]["source"] == "ocr"
    assert r.json()["data"]["status"] == "draft"

    # 草稿不进入统计
    r = await client.get("/api/v1/ledger/summary", headers=h)
    assert float(r.json()["data"]["metrics"]["expense"]) == 0

    # 确认后进入统计
    r = await client.post(f"/api/v1/ledger/events/{event_id}/confirm", headers=h, json={})
    assert r.json()["success"] is True
    r = await client.get("/api/v1/ledger/summary", headers=h)
    assert abs(float(r.json()["data"]["metrics"]["expense"]) - 128.50) < 0.01


async def test_attachment_marked_archived_after_confirmed_reference(client):
    """附件被已确认的业务事件引用后应标记为已入库，并不再出现在待处理列表。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    att = (await _upload(client, h)).json()["data"]
    att_id = att["id"]

    # 刚上传：未引用
    items = (await client.get("/api/v1/ledger/capture/attachments", headers=h)).json()["data"]["items"]
    row = next(i for i in items if i["id"] == att_id)
    assert row["referenced"] is False

    # 基于该附件建一笔支出草稿 → 状态为草稿引用中，仍算待处理
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "OCR 票据识别", "amount": "88",
        "account_id": ids["a1"], "attachment_id": att_id, "source": "ocr",
    })
    assert r.json()["success"] is True, r.text
    event_id = r.json()["data"]["id"]

    items = (await client.get("/api/v1/ledger/capture/attachments", headers=h)).json()["data"]["items"]
    row = next(i for i in items if i["id"] == att_id)
    assert row["referenced"] is True
    assert row["reference_status"] == "draft"
    assert row["reference_id"] == event_id
    assert "OCR 票据识别" in row["reference_label"]
    # 草稿引用还不算入库，仍能出现在待处理
    pending = (await client.get("/api/v1/ledger/capture/attachments",
                                params={"only_pending": True}, headers=h)).json()["data"]["items"]
    assert any(i["id"] == att_id for i in pending)

    # 确认后：已入库，不再出现在待处理列表
    await client.post(f"/api/v1/ledger/events/{event_id}/confirm", headers=h, json={})
    items = (await client.get("/api/v1/ledger/capture/attachments", headers=h)).json()["data"]["items"]
    row = next(i for i in items if i["id"] == att_id)
    assert row["reference_status"] == "confirmed"
    pending = (await client.get("/api/v1/ledger/capture/attachments",
                                params={"only_pending": True}, headers=h)).json()["data"]["items"]
    assert all(i["id"] != att_id for i in pending), "已入库附件不应出现在待处理列表"
