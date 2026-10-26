"""信用卡不是债务：即使存在 credit_card 负债记录，也不能重复扣减净资产。

场景：账户里的信用卡余额已经是负数（欠款），若负债域再录一笔 credit_card，
净值会被扣两次。此测试锁定「credit_card 不计入负债合计」的规则。
"""

from __future__ import annotations

from tests.conftest import auth, complete_setup


async def _net_worth(client, h) -> dict:
    return (await client.get("/api/v1/ledger/summary", headers=h,
                             params={"period": "all"})).json()["data"]["net_worth"]


async def test_credit_card_liability_not_counted_as_debt(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    base = await _net_worth(client, h)
    assert base["included_liabilities"] == 0

    # 普通债务：计入
    await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "消费贷", "liability_type": "personal", "currency": "CNY",
        "initial_amount": "30000",
    })
    after_debt = await _net_worth(client, h)
    assert after_debt["included_liabilities"] == 1
    assert after_debt["total_liabilities"] == "30000.00"

    # 信用卡记录：不计入负债合计
    await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "某银行信用卡", "liability_type": "credit_card", "currency": "CNY",
        "initial_amount": "20000",
    })
    after_card = await _net_worth(client, h)
    assert after_card["included_liabilities"] == 1, after_card
    assert after_card["total_liabilities"] == "30000.00", after_card
    assert after_card["net_worth"] == after_debt["net_worth"]
