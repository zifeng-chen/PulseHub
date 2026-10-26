"""租赁合同模板下载（.docx）。

合同是自拼 OOXML 生成的：这里既验证接口能下载，也把 zip 解开确认正文里
确实带上了租约信息（否则用户下载到一份空模板也不知道）。
"""

from __future__ import annotations

import io
import zipfile

from tests.conftest import auth, complete_setup

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def _bootstrap(client) -> dict:
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    r = await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "real_estate", "name": "首创悦都45号", "currency": "CNY",
        "acquire_cost": "3000000", "current_value": "3200000",
        "extension": {
            "property_type": "residence", "building_area": "89.5",
            "address": "北京市朝阳区某某路 8 号院 3 号楼 1201",
            "title_no": "京(2020)东不动产权第 0001 号",
        },
    })
    assert r.json()["success"] is True, r.text
    return {"headers": h, "asset_id": r.json()["data"]["id"]}


def _document_text(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        assert zf.testzip() is None
        return zf.read("word/document.xml").decode("utf-8")


async def test_blank_contract_template_downloads(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    r = await client.get("/api/v1/ledger/leases/contract-template", headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith(DOCX_MIME)
    assert "attachment" in r.headers["content-disposition"]
    text = _document_text(r.content)
    assert "房屋租赁合同" in text
    # 空白模板必须有可填写的空位
    assert "＿＿" in text


async def test_lease_contract_is_prefilled(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/leases", headers=h, json={
        "real_estate_id": ids["asset_id"], "tenant_name": "张三",
        "tenant_contact": "13800001111", "monthly_rent": "6800",
        "payment_frequency": "monthly", "currency": "CNY",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
        "deposit_amount": "13600", "property_repair_bearer": "landlord",
    })
    assert r.json()["success"] is True, r.text
    lease_id = r.json()["data"]["id"]

    r = await client.get(f"/api/v1/ledger/leases/{lease_id}/contract", headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith(DOCX_MIME)
    text = _document_text(r.content)
    # 租约里的已知信息应当出现在合同正文里
    assert "张三" in text
    assert "13800001111" in text
    assert "6,800.00" in text
    assert "13,600.00" in text
    assert "北京市朝阳区某某路 8 号院 3 号楼 1201" in text
    assert "89.50 平方米" in text
    assert "2026 年 1 月 1 日" in text
