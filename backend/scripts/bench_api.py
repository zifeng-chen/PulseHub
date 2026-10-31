"""逐个测所有 GET 接口的延迟（本地实例，含 uvicorn + 序列化开销）。

方法：
- 从 /openapi.json 拿全部路由，只测 GET（读接口，不改数据）；
- 先用列表接口把 id 收集起来，替换路径参数；取不到 id 的接口跳过并记录原因；
- 每个接口 3 次预热 + 7 次采样，取中位数与 p95，避免首请求建连/缓存干扰。
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import statistics
import time

import httpx

BASE = os.environ.get("BENCH_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("BENCH_USER", "bench")
PWD = os.environ.get("BENCH_PWD", "Bench!23456")
ROUNDS = 7
WARMUP = 3

# 这些查询参数给上默认值，否则接口会 422（测不出真实耗时）
DEFAULT_QUERY = {
    "period": "current_month", "currency": "CNY", "page": 1, "page_size": 20,
    "reveal": "true", "months": 6, "status": "active", "scope": "overview",
}


async def collect_params(c: httpx.AsyncClient) -> dict[str, str]:
    """把各类实体的一个 id 抓出来，用于填充路径参数。"""
    ids: dict[str, str] = {}

    async def get(path: str, **params):
        r = await c.get(path, params=params or None)
        body = r.json()
        return body.get("data") if body.get("success") else None

    accounts = (await get("/ledger/accounts") or {}).get("items") or []
    if accounts:
        ids["account_id"] = accounts[0]["id"]
        credit = next((a for a in accounts if a.get("credit_limit")), None)
        if credit:
            ids["credit_account_id"] = credit["id"]
            # 造一张账单，才能测「账单列表」
            r = await c.post(f"/ledger/accounts/{credit['id']}/card-bills", json={})
            if r.json().get("success"):
                ids["card_account_id"] = credit["id"]

    events = (await get("/ledger/events", page_size=1) or {}).get("items") or []
    if events:
        ids["event_id"] = events[0]["id"]

    liabilities = (await get("/ledger/liabilities") or {}).get("items") or []
    if liabilities:
        ids["liability_id"] = liabilities[0]["id"]

    assets = (await get("/ledger/assets", page_size=50) or {}).get("items") or []
    if assets:
        ids["asset_id"] = assets[0]["id"]
        vehicle = next((a for a in assets if a.get("asset_type") == "vehicle"), None)
        if vehicle:
            ids["vehicle_id"] = vehicle["id"]

    invest = (await get("/ledger/investments") or {}).get("items") or []
    if invest:
        ids["investment_id"] = invest[0]["id"]

    audit = (await get("/audit", page=1, page_size=1) or {}).get("items") or []
    if audit:
        ids["log_id"] = audit[0]["id"]

    # 下面这些实体由 bench_seed.py 补齐，缺了就让对应接口跳过（不硬造无效 id）
    more = {
        "realestate_id": "/ledger/assets/real-estates",
        "lease_id": "/ledger/leases",
        "credit_card_id": "/ledger/credit-cards",
        "attachment_id": "/ledger/capture/attachments",
        "batch_id": "/ledger/imports",
        "conversation_id": "/ai/conversations",
        "provider_id": "/settings/ai-providers",
        "backup_id": "/settings/backup",
    }
    for key, path in more.items():
        data = await get(path)
        if not isinstance(data, dict):
            continue
        items = data.get("items")
        if items is None:
            # 有些接口直接返回 {backups: [...]} / {providers: [...]} 这类结构
            for value in data.values():
                if isinstance(value, list) and value and isinstance(value[0], dict) and value[0].get("id"):
                    items = value
                    break
        if items:
            ids[key] = items[0]["id"]

    return ids


def resolve(path: str, ids: dict[str, str]) -> tuple[str | None, str]:
    """把 {xxx} 换成真实 id。同名参数在不同模块指向不同实体（投资接口的
    {account_id} 是投资账户，不是银行账户），所以按路径前缀选 id。"""
    def pick(name: str) -> str | None:
        if name == "account_id":
            if "/investments" in path:
                return ids.get("investment_id")
            return ids.get("account_id")
        if name == "asset_id":
            if "vehicles" in path:
                return ids.get("vehicle_id")
            if "real-estates" in path:
                return ids.get("realestate_id")
            return ids.get("asset_id")
        if name == "card_id":
            return ids.get("credit_card_id")
        return ids.get(name)

    out = path
    missing: list[str] = []
    for token in [seg for seg in path.split("/") if seg.startswith("{")]:
        name = token.strip("{}")
        value = pick(name)
        if not value:
            missing.append(name)
        else:
            out = out.replace(token, value)
    if missing:
        return None, ",".join(missing)
    return out, ""


async def main() -> int:
    root = BASE.split('/api/v1')[0]
    async with httpx.AsyncClient(base_url=BASE, timeout=120) as c:
        login = (await c.post("/auth/login", json={
            "username": USER, "password": PWD, "remember_device": True,
        })).json()["data"]
        c.headers["Authorization"] = f"Bearer {login['access_token']}"

        spec = (await c.get(f"{root}/openapi.json")).json()
        ids = await collect_params(c)
        print(f"可用 id：{', '.join(sorted(ids))}")

        rows: list[dict] = []
        skipped: list[tuple[str, str]] = []

        for path, ops in sorted(spec["paths"].items()):
            if "get" not in ops:
                continue
            url, missing = resolve(path, ids)
            if not url:
                skipped.append((path, f"缺少 {missing}"))
                continue

            # 只给该接口声明过的查询参数填默认值
            declared = {p["name"] for p in ops["get"].get("parameters", []) if p.get("in") == "query"}
            params = {k: v for k, v in DEFAULT_QUERY.items() if k in declared}
            if path.endswith("/prepay-simulation"):
                params["amount"] = "10000"
            if path.endswith("/reconciliation"):
                params["account_id"] = ids.get("account_id")
                params.pop("reveal", None)

            # 预热
            status = 0
            for _ in range(WARMUP):
                r = await c.get(root + url, params=params)
                status = r.status_code

            times: list[float] = []
            for _ in range(ROUNDS):
                t0 = time.perf_counter()
                r = await c.get(root + url, params=params)
                times.append((time.perf_counter() - t0) * 1000)
                status = r.status_code

            rows.append({
                "path": path,
                "query": params or None,
                "status": status,
                "median": round(statistics.median(times), 1),
                "min": round(min(times), 1),
                "p95": round(sorted(times)[max(0, int(len(times) * 0.95) - 1)], 1),
                "max": round(max(times), 1),
            })

        rows.sort(key=lambda x: -x["median"])
        out = {"base": BASE, "rounds": ROUNDS, "rows": rows,
               "skipped": [{"path": p, "reason": r} for p, r in skipped]}
        out_path = pathlib.Path(os.environ.get("BENCH_OUT", ".tmp/bench.json"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)

        print(f"\n共测 {len(rows)} 个 GET 接口，跳过 {len(skipped)} 个\n")
        print(f"{'接口':<58}{'状态':>5}{'中位':>8}{'p95':>8}")
        for r in rows:
            print(f"{r['path']:<58}{r['status']:>5}{r['median']:>8}{r['p95']:>8}")
        if skipped:
            print("\n跳过：")
            for p, reason in skipped:
                print(f"  {p} —— {reason}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
