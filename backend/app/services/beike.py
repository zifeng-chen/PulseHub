"""贝壳开放平台（open.ke.com）适配服务：OAuth2.0 + 房屋估价。

官方接口：
- 取 token：POST {base}/oauth/token，form: grant_type=client_credentials & client_id & client_secret
- 估价：POST {base}/api/assessPrice，header: access_token，form:
  assessType / standPriceAssessBuildArea / standDetailedAddress / standCity / cityGbCode ...

状态与「测试」都是真实调用：只有成功拿到 token 才算通过；每次估价都会写回状态。
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterStatus
from app.core.crypto import decrypt_secret
from app.core.errors import AppError, ErrorCode
from app.core.ids import utc_iso
from app.db.models.config import BeikeConfig

_TIMEOUT = 15.0

# 额度类失败：鉴权没问题，只是这个接口没额度/次数用完了
_QUOTA_PATTERN = re.compile(
    r"配额|额度|次数(?:已)?(?:用完|不足|为0)|限额|余额不足|quota|out of credit", re.I
)

CONSOLE_URL = "https://open.ke.com/"


def _is_quota_error(message: str) -> bool:
    return bool(_QUOTA_PATTERN.search(message or ""))


def _with_trace(text: str, body: dict) -> str:
    """把贝壳的 traceId 带上，找客服排查额度/权限问题时能直接定位这一次调用。"""
    trace = (body or {}).get("traceId")
    return f"{text}（traceId: {trace}）" if trace else text
_TOKEN_CACHE: dict[str, tuple[str, float, int]] = {}  # app_key -> (token, expires_at, ttl)


async def get_config(db: AsyncSession, household_id: str | None) -> BeikeConfig | None:
    return (await db.execute(
        select(BeikeConfig).where(BeikeConfig.household_id == household_id)
    )).scalars().first()


def _fmt_ts(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


async def _record_status(cfg_id: str, ok: bool, detail: str, status: str | None = None) -> None:
    """独立会话写状态，业务失败也能如实记录。"""
    from app.db.base import SessionLocal

    try:
        async with SessionLocal() as session:
            await session.execute(update(BeikeConfig).where(BeikeConfig.id == cfg_id).values(
                last_status=status or ("ok" if ok else "failed"),
                last_call_at=datetime.now(timezone.utc),
                last_error=None if ok else detail[:500],
            ))
            await session.commit()
    except Exception:  # noqa: BLE001 - 状态写入失败不影响业务
        pass


async def _fetch_token(cfg: BeikeConfig) -> tuple[str, int]:
    app_key = decrypt_secret(cfg.app_key_cipher or "")
    app_secret = decrypt_secret(cfg.app_secret_cipher or "")
    now = time.monotonic()
    cached = _TOKEN_CACHE.get(app_key)
    if cached and cached[1] > now:
        return cached[0], cached[2]

    base = (cfg.base_url or "https://gw-open.ke.com").rstrip("/")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{base}/oauth/token",
            data={"grant_type": "client_credentials",
                  "client_id": app_key, "client_secret": app_secret},
        )
    try:
        body = resp.json()
    except ValueError:
        body = {}
    data = body.get("data") or {}
    token = data.get("access_token")
    if resp.status_code >= 400 or body.get("code") not in (0, None) or not token:
        reason = body.get("msg") or f"HTTP {resp.status_code}"
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE,
                       f"贝壳鉴权失败：{reason}（请核对 AppKey / AppSecret）", status_code=502)
    ttl = int(data.get("expires_in") or 3600)
    _TOKEN_CACHE[app_key] = (token, now + max(ttl - 60, 60), ttl)
    return token, ttl


async def status(db: AsyncSession, household_id: str | None) -> AdapterStatus:
    cfg = await get_config(db, household_id)
    if cfg is None or not cfg.app_key_cipher or not cfg.app_secret_cipher:
        return AdapterStatus(key="beike", label="贝壳房产数据",
                             status="unconfigured", detail="尚未配置 AppKey / AppSecret")
    if not cfg.enabled:
        return AdapterStatus(key="beike", label="贝壳房产数据", status="disabled",
                             detail="已配置但未启用")

    checked = _fmt_ts(cfg.last_call_at)
    extra = {"default_city": cfg.default_city, "default_city_code": cfg.default_city_code,
             "last_status": cfg.last_status}
    if cfg.last_status == "quota":
        # 鉴权是通的，只是接口额度用完：不能显示成「连接失败」，否则会误导排查方向
        return AdapterStatus(
            key="beike", label="贝壳房产数据", status="connected",
            detail="鉴权正常，但估价接口额度已用完"
                   + (f"（{checked}）" if checked else "")
                   + "；请在贝壳开放平台控制台查看剩余额度，额度恢复前可手工填写估值。",
            last_checked_at=utc_iso(cfg.last_call_at) if cfg.last_call_at else None,
            extra=extra,
        )
    if cfg.last_status == "failed":
        return AdapterStatus(
            key="beike", label="贝壳房产数据", status="failed",
            detail=f"最近一次调用失败：{cfg.last_error or '未知原因'}"
                   + (f"（{checked}）" if checked else ""),
            last_checked_at=utc_iso(cfg.last_call_at) if cfg.last_call_at else None,
            extra=extra,
        )
    if cfg.last_status == "ok":
        return AdapterStatus(
            key="beike", label="贝壳房产数据", status="connected",
            detail="最近一次调用成功" + (f"（{checked}）" if checked else ""),
            last_checked_at=utc_iso(cfg.last_call_at) if cfg.last_call_at else None,
            extra=extra,
        )
    return AdapterStatus(
        key="beike", label="贝壳房产数据", status="untested",
        detail="已配置并启用，但还没有真实调用记录；点「测试连接」验证",
        extra=extra,
    )


async def test(db: AsyncSession, household_id: str | None) -> AdapterStatus:
    cfg = await get_config(db, household_id)
    if cfg is None or not cfg.app_key_cipher or not cfg.app_secret_cipher:
        raise AppError(ErrorCode.PROVIDER_NOT_CONFIGURED,
                       "贝壳开放平台尚未配置，请填写 AppKey / AppSecret", status_code=409)
    checked = utc_iso(datetime.now(timezone.utc))
    try:
        token, ttl = await _fetch_token(cfg)
    except AppError as exc:
        detail = exc.message if hasattr(exc, "message") else str(exc)
        await _record_status(cfg.id, False, detail)
        return AdapterStatus(key="beike", label="贝壳房产数据", status="failed",
                             detail=detail, last_checked_at=checked)
    except Exception as exc:  # noqa: BLE001
        detail = f"连接失败：{exc.__class__.__name__}"
        await _record_status(cfg.id, False, detail)
        return AdapterStatus(key="beike", label="贝壳房产数据", status="failed",
                             detail=detail, last_checked_at=checked)

    detail = f"鉴权通过（token 有效期 {ttl} 秒）"
    await _record_status(cfg.id, True, detail)
    return AdapterStatus(key="beike", label="贝壳房产数据", status="connected",
                         detail=detail, last_checked_at=checked)


async def assess_price(
    db: AsyncSession, household_id: str | None, *,
    city: str, city_code: str, address: str, area: str,
    district: str | None = None, resblock: str | None = None,
    building: str | None = None, unit: str | None = None, floor: str | None = None,
    assess_type: str = "HOUSE_CERT_ASSESS",
) -> dict:
    """调用贝壳壳e估：返回单价、总价、价格维度与预警。"""
    cfg = await get_config(db, household_id)
    if cfg is None or not cfg.app_key_cipher or not cfg.app_secret_cipher:
        raise AppError(ErrorCode.PROVIDER_NOT_CONFIGURED,
                       "贝壳开放平台尚未配置，请前往设置填写 AppKey / AppSecret", status_code=409)
    if not cfg.enabled:
        raise AppError(ErrorCode.PROVIDER_NOT_CONFIGURED,
                       "贝壳开放平台已停用，请前往设置启用", status_code=409)

    try:
        token, _ = await _fetch_token(cfg)
    except AppError as exc:
        detail = exc.message if hasattr(exc, "message") else str(exc)
        await _record_status(cfg.id, False, detail)
        raise

    base = (cfg.base_url or "https://gw-open.ke.com").rstrip("/")
    form = {
        "assessType": assess_type,
        "standPriceAssessBuildArea": str(area),
        "standDetailedAddress": address,
        "standCity": city,
        "cityGbCode": str(city_code),
    }
    for key, value in {
        "standDistrictAndCounty": district, "standResblock": resblock,
        "standBuilding": building, "standUnit": unit, "standFloor": floor,
    }.items():
        if value:
            form[key] = value

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{base}/api/assessPrice", data=form,
                                     headers={"access_token": token})
    except httpx.HTTPError as exc:
        detail = f"估价请求失败：{exc.__class__.__name__}"
        await _record_status(cfg.id, False, detail)
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, detail, status_code=502)

    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code >= 400 or body.get("code") not in (0, None):
        reason = body.get("msg") or f"HTTP {resp.status_code}"
        if _is_quota_error(reason):
            detail = _with_trace(f"估价接口额度已用完：{reason}", body)
            await _record_status(cfg.id, False, detail, status="quota")
            raise AppError(
                ErrorCode.QUOTA_EXCEEDED,
                f"贝壳估价额度已用完（{reason}）：请到贝壳开放平台控制台 → 我的应用 → 接口管理，"
                "查看「房屋价值分析报告 / 壳e估」的剩余额度或申请/购买更多额度。"
                "额度恢复前可以手工填写估值，不影响资产与净资产计算。"
                + (f" 排查凭证 traceId：{body.get('traceId')}" if body.get("traceId") else ""),
                status_code=429,
            )
        detail = _with_trace(f"估价失败：{reason}", body)
        await _record_status(cfg.id, False, detail)
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, detail, status_code=502)

    data = body.get("data") or {}
    if not data.get("avgPrice") and not data.get("priceForHouse"):
        detail = "估价未返回有效价格（可能地址无法匹配）"
        await _record_status(cfg.id, False, detail)
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, detail, status_code=502)

    await _record_status(cfg.id, True, "估价成功")
    return {
        "avg_price": data.get("avgPrice"),
        "total_price": data.get("priceForHouse"),
        "price_dim": data.get("avgPriceDim"),
        "warning": data.get("warningAlert") or "",
        "row_key": data.get("rowKey"),
        "trace_id": body.get("traceId"),
    }
