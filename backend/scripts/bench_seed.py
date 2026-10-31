"""给接口压测造一份「有体量」的数据：走真实 API，保证账务自洽。

为什么不用空库测：空库所有查询都是 2ms，看不出瓶颈；这里造 10 个账户、
600 笔流水、8 笔负债、2 个投资账户与若干资产，接近一个家庭用一年的量级。
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
from datetime import date, datetime, timedelta

import httpx

BASE = os.environ.get("BENCH_BASE", "http://127.0.0.1:8000/api/v1")
random.seed(20261031)

ADMIN = {"display_name": "压测用户", "username": "bench", "password": "Bench!23456",
         "confirm_password": "Bench!23456"}


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        r = await c.post("/setup/database", json={
            "use_embedded_dev_database": True, "host": "127.0.0.1", "port": 3306,
            "database_name": "fam", "username": "root", "password": "x",
            "ssl_enabled": False, "connect_timeout": 5,
        })
        if not r.json().get("success"):
            print("setup/database 失败:", r.text[:200])
        await c.post("/setup/admin", json=ADMIN)
        await c.post("/setup/household", json={
            "name": "压测家庭", "base_currency": "CNY",
            "timezone": "Asia/Shanghai", "is_multi_member": True,
        })
        await c.post("/setup/finance", json={
            "asset_purchase_separate": True, "investment_valuation_in_net_worth": True,
        })
        await c.post("/setup/complete")

        login = (await c.post("/auth/login", json={
            "username": ADMIN["username"], "password": ADMIN["password"], "remember_device": True,
        })).json()["data"]
        c.headers["Authorization"] = f"Bearer {login['access_token']}"

        async def post(path: str, payload: dict, label: str) -> dict | None:
            r = await c.post(path, json=payload)
            body = r.json()
            if not body.get("success"):
                print(f"  ! {label} 失败 {r.status_code}: {str(body.get('error'))[:120]}")
                return None
            return body["data"]

        # ---- 账户：4 银行（1 张信用卡）+ 3 电子钱包 + 1 现金 + 2 投资 ----
        accounts: list[dict] = []
        for i, (name, inst, opening, limit) in enumerate([
            ("招商储蓄卡", "招商银行", "180000", None),
            ("建设工资卡", "中国建设银行", "26000", None),
            ("工商储蓄卡", "中国工商银行", "52000", None),
            ("招商信用卡", "招商银行", "-8600", "80000"),
            ("支付宝", "支付宝", "3200", None),
            ("微信零钱", "微信支付", "1800", None),
            ("云闪付钱包", "云闪付", "600", None),
            ("现金", None, "1500", None),
        ]):
            payload = {"name": name, "account_type": "bank" if inst not in (None, "支付宝", "微信支付", "云闪付") else "e_wallet",
                       "currency": "CNY", "opening_balance": opening, "card_last4": f"62{i:02d}"}
            if inst:
                payload["institution"] = inst
            if limit:
                payload["credit_limit"] = limit
                payload["bill_day"] = 5
                payload["due_day"] = 23
            if name == "现金":
                payload["account_type"] = "cash"
                payload.pop("card_last4", None)
            data = await post("/ledger/accounts", payload, f"账户 {name}")
            if data:
                accounts.append(data)
        print(f"账户 {len(accounts)} 个")

        categories = (await c.get("/ledger/categories")).json()["data"]["items"]
        expense_cats = [x for x in categories if x.get("domain") == "expense"][:12]
        income_cats = [x for x in categories if x.get("domain") == "income"][:5]

        # ---- 流水：180 天，600 笔 ----
        today = date.today()
        created = 0
        for i in range(600):
            day = today - timedelta(days=random.randint(0, 179))
            kind = random.choices(["expense", "income"], weights=[0.85, 0.15])[0]
            amount = f"{random.randint(15, 4800)}.{random.randint(0, 99):02d}"
            payload = {
                "event_type": kind,
                "title": f"{'消费' if kind == 'expense' else '收入'} {i}",
                "amount": amount,
                "account_id": random.choice(accounts)["id"],
                "occurred_at": f"{day.isoformat()}T{random.randint(8, 22):02d}:{random.randint(0, 59):02d}:00",
            }
            cat = random.choice(expense_cats if kind == "expense" else income_cats)
            if cat:
                payload["category_id"] = cat["id"]
            data = await post("/ledger/events", payload, f"流水 {i}")
            if not data:
                continue
            r = await c.post(f"/ledger/events/{data['id']}/confirm", json={"version": data["version"]})
            if r.json().get("success"):
                created += 1
        print(f"已确认流水 {created} 笔")

        # ---- 转账：24 笔 ----
        transfers = 0
        for i in range(24):
            src, dst = random.sample(accounts[:3], 2)
            data = await post("/ledger/transfers", {
                "from_account_id": src["id"], "to_account_id": dst["id"],
                "amount": f"{random.randint(200, 6000)}.00",
                "occurred_at": f"{(today - timedelta(days=random.randint(0, 120))).isoformat()}T10:00:00",
            }, f"转账 {i}")
            if data:
                transfers += 1
        print(f"转账 {transfers} 笔")

        # ---- 负债 8 笔 + 20 次还款 ----
        liabilities = []
        for i, (name, amount, rate) in enumerate([
            ("房贷", "980000", "4.2"), ("车贷", "120000", "3.6"), ("消费贷", "60000", "6.8"),
            ("装修贷", "80000", "5.1"), ("信用贷", "50000", "9.2"), ("亲戚借款", "30000", "0"),
            ("家电分期", "12000", "4.5"), ("教育分期", "24000", "3.9"),
        ]):
            data = await post("/ledger/liabilities", {
                "name": name, "liability_type": "personal", "currency": "CNY",
                "initial_amount": amount, "interest_rate": rate,
                "payment_frequency": "monthly", "payment_day": 10,
                "term_months": 240 if i == 0 else 36,
            }, f"负债 {name}")
            if data:
                liabilities.append(data)
        repayments = 0
        for i in range(20):
            li = random.choice(liabilities)["id"]
            data = await post(f"/ledger/liabilities/{li}/repayments", {
                "amount": f"{random.randint(800, 6800)}.00",
                "account_id": accounts[0]["id"],
            }, f"还款 {i}")
            if data:
                repayments += 1
        print(f"负债 {len(liabilities)} 笔，还款 {repayments} 次")

        # ---- 资产：尽量建不动产/车辆（扩展字段必填就跳过） ----
        assets = []
        asset_payloads = [
            {"name": "自住房", "asset_type": "real_estate", "currency": "CNY", "current_value": "3200000",
             "extension": {"property_type": "residential", "area": 118, "address": "上海"}},
            {"name": "代步车", "asset_type": "vehicle", "currency": "CNY", "current_value": "180000",
             "extension": {"brand": "比亚迪", "plate_no": "沪A12345"}},
            {"name": "金条", "asset_type": "precious_metal", "currency": "CNY", "current_value": "52000",
             "extension": {"metal": "gold", "weight": 80, "purity": "999"}},
        ]
        for payload in asset_payloads:
            data = await post("/ledger/assets", payload, f"资产 {payload['name']}")
            if data:
                assets.append(data if isinstance(data, dict) else data)

        # ---- 投资：2 个账户 + 30 笔交易 + 6 次估值 ----
        invest = []
        for name, kind in [("证券账户", "securities"), ("基金账户", "fund")]:
            data = await post("/ledger/investments", {
                "name": name, "account_type": kind, "currency": "CNY", "opening_cash": "50000",
            }, f"投资账户 {name}")
            if data:
                invest.append(data)
        trades = 0
        for i in range(30):
            acc = random.choice(invest)["id"] if invest else None
            if not acc:
                break
            data = await post(f"/ledger/investments/{acc}/trades", {
                "symbol": f"6000{random.randint(10, 99)}",
                "name": f"测试股票{i}",
                "trade_type": random.choice(["buy", "sell"]),
                "quantity": f"{random.randint(1, 20)}00",
                "price": f"{random.randint(5, 60)}.{random.randint(0, 99):02d}",
                "traded_at": f"{(today - timedelta(days=random.randint(0, 150))).isoformat()}T14:30:00",
            }, f"交易 {i}")
            if data:
                trades += 1
        print(f"资产 {len(assets)} 项，投资账户 {len(invest)} 个，交易 {trades} 笔")

        # ---- 补齐「之前测不到」的实体：房产、租约、负债型信用卡、附件、导入批次、AI 会话/服务商、备份 ----
        # 目的：让全部 GET 接口都有真实数据可测，而不是跳过；这些数据只在压测库里，跑完整个库删掉。
        async def post_form(path: str, files: dict, data: dict, label: str):
            r = await c.post(path, files=files, data=data)
            body = r.json()
            if not body.get("success"):
                print(f"  ! {label} 失败 {r.status_code}: {str(body.get('error'))[:120]}")
                return None
            return body["data"]

        # 房产（租约与水电煤账户都要挂在它下面）
        realestate_id = None
        estate = await post("/ledger/assets", {
            "name": "出租房·浦东", "asset_type": "real_estate", "currency": "CNY",
            "current_value": "2600000",
            "extension": {"property_type": "residence", "area": 89, "address": "上海市浦东新区"},
        }, "出租房")
        if estate:
            realestate_id = (estate[0] if isinstance(estate, list) else estate).get("id")
            print("房产 id:", realestate_id)

        # 租约
        lease_id = None
        if realestate_id:
            lease = await post("/ledger/leases", {
                "real_estate_id": realestate_id, "tenant_name": "张先生",
                "monthly_rent": "6500.00", "deposit_amount": "13000.00",
                "payment_frequency": "monthly", "payment_day": 5,
                "start_date": f"{(today - timedelta(days=200)).isoformat()}",
                "end_date": f"{(today + timedelta(days=165)).isoformat()}",
                "receiving_account_id": accounts[0]["id"], "status": "active",
            }, "租约")
            if lease:
                lease_id = lease.get("id")
                print("租约 id:", lease_id)

        # 负债型信用卡（有 card_id 才能测 /credit-cards/{id}）
        card = await post("/ledger/liabilities", {
            "name": "中信信用卡", "liability_type": "credit_card", "currency": "CNY",
            "initial_amount": "0", "extension": {
                "credit_limit": "60000", "bill_day": 8, "due_day": 26, "issuer": "中信银行",
            },
        }, "负债型信用卡")
        if card:
            print("信用卡负债 id:", card.get("id"))

        # 附件（上传一个小文本文件即可）
        attach_id = None
        if accounts:
            # 附件只收 jpeg/jpg/pdf/png，用一张 1×1 的 PNG 占位
            png = bytes.fromhex(
                "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                "1f15c4890000000d4944415478da63f8cfc0000003010100b5a2f6b70000"
                "000049454e44ae426082"
            )
            r = await c.post("/ledger/capture/attachments",
                             files={"file": ("receipt.png", png, "image/png")},
                             data={"material_type": "receipt", "object_type": "account",
                                   "object_id": accounts[0]["id"], "note": "压测附件"})
            body = r.json()
            if body.get("success"):
                attach_id = body["data"]["id"]
                print("附件 id:", attach_id)
            else:
                print("  ! 附件上传失败:", str(body.get("error"))[:120])

        # 导入批次（CSV：类型/标题/金额/日期/账户）
        batch_id = None
        if accounts:
            csv_text = "类型,标题,金额,日期,账户\n" + "\n".join(
                f"支出,导入测试{i},{100 + i}.00,{(today - timedelta(days=i)).isoformat()},{accounts[0]['name']}"
                for i in range(5)
            )
            batch = await post("/ledger/imports", {"file_name": "bench.csv", "content": csv_text}, "导入批次")
            if batch:
                batch_id = batch.get("id") or (batch.get("batch") or {}).get("id")
                print("导入批次 id:", batch_id)

        # AI 会话
        conv = await post("/ai/conversations", {"title": "压测对话"}, "AI 会话")
        if conv:
            print("会话 id:", conv.get("id"))

        # 第三方 AI 服务商（不启用，仅用于测列表与出网预览）
        provider = await post("/settings/ai-providers", {
            "name": "压测服务商", "base_url": "https://api.example.com", "api_key": "sk-benchmark-only",
            "model": "gpt-4o-mini",
        }, "AI 服务商")
        if provider:
            print("服务商 id:", provider.get("id"))

        # 备份（需要二次确认令牌）
        tok_r = await c.post("/settings/confirm-tokens",
                             json={"action": "backup.create", "object_type": "backup"})
        token = (tok_r.json().get("data") or {}).get("token") if tok_r.json().get("success") else None
        if token:
            backup = await post("/settings/backup/create", {"confirm_token": token}, "备份")
            if backup:
                print("备份 id:", backup.get("id"))

        # 统计最终体量
        events = (await c.get("/ledger/events", params={"page_size": 1})).json()["data"]
        print("最终体量：流水总数", events.get("total"))
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
