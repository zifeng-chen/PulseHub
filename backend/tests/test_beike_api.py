"""贝壳开放平台接入：OAuth2.0 鉴权、估价调用与真实状态写回。"""

from __future__ import annotations

from sqlalchemy import select

from app.core.crypto import encrypt_secret
from app.core.errors import AppError, ErrorCode
from app.db.base import SessionLocal
from app.db.models.config import BeikeConfig
from app.services import beike
from tests.conftest import complete_setup


class _Resp:
    def __init__(self, payload: dict, status_code: int = 200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    token_payload: dict = {"code": 0, "data": {"access_token": "tok-123", "expires_in": 3600}}
    assess_payload: dict = {
        "code": 0, "traceId": "t-1",
        "data": {"avgPrice": 47082.88, "priceForHouse": 3121595.22,
                 "avgPriceDim": "resblock", "warningAlert": "", "rowKey": "r-1"},
    }

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, data=None, headers=None, **kwargs):
        if "oauth/token" in url:
            return _Resp(self.token_payload)
        return _Resp(self.assess_payload)


async def _seed(enabled: bool = True) -> None:
    async with SessionLocal() as db:
        db.add(BeikeConfig(
            id="cfg-beike", household_id=None,
            app_key_cipher=encrypt_secret("ak-test"),
            app_secret_cipher=encrypt_secret("sk-test"),
            base_url="https://gw-open.ke.com", enabled=enabled,
        ))
        await db.commit()


async def _status():
    async with SessionLocal() as db:
        return await beike.status(db, None)


async def test_beike_status_semantics(client):
    tokens = await complete_setup(client)
    assert tokens  # 初始化家庭
    s = await _status()
    assert s.status == "unconfigured"

    await _seed(enabled=False)
    s = await _status()
    assert s.status == "disabled"

    async with SessionLocal() as db:
        cfg = (await db.execute(select(BeikeConfig))).scalars().first()
        cfg.enabled = True
        await db.commit()
    s = await _status()
    assert s.status == "untested"


async def test_beike_test_and_assess_are_real_calls(client, monkeypatch):
    await complete_setup(client)
    await _seed()
    beike._TOKEN_CACHE.clear()
    monkeypatch.setattr("app.services.beike.httpx.AsyncClient", _FakeClient)

    async with SessionLocal() as db:
        st = await beike.test(db, None)
        assert st.status == "connected", st
        assert "鉴权通过" in st.detail
        cfg = (await db.execute(select(BeikeConfig))).scalars().first()
        assert cfg.last_status == "ok"

        result = await beike.assess_price(
            db, None, city="北京市", city_code="110000",
            address="北京市昌平区龙乡小区东区14号楼1单元601", area="66.3",
        )
    assert result["avg_price"] == 47082.88
    assert result["total_price"] == 3121595.22
    assert result["price_dim"] == "resblock"


async def test_beike_failure_is_reported_and_persisted(client, monkeypatch):
    await complete_setup(client)
    await _seed()
    beike._TOKEN_CACHE.clear()
    _FakeClient.token_payload = {"code": 40001, "msg": "invalid client"}

    try:
        monkeypatch.setattr("app.services.beike.httpx.AsyncClient", _FakeClient)
        async with SessionLocal() as db:
            st = await beike.test(db, None)
        assert st.status == "failed"
        assert "invalid client" in st.detail

        async with SessionLocal() as db:
            cfg = (await db.execute(select(BeikeConfig))).scalars().first()
            assert cfg.last_status == "failed"
            assert "invalid client" in (cfg.last_error or "")
    finally:
        _FakeClient.token_payload = {"code": 0, "data": {"access_token": "tok-123", "expires_in": 3600}}


async def test_beike_quota_exhausted_is_actionable(client, monkeypatch):
    """额度用完：错误码可识别、状态写 quota、连接状态不能显示成失败。"""
    await complete_setup(client)
    await _seed()
    beike._TOKEN_CACHE.clear()
    _FakeClient.assess_payload = {"code": 40003, "msg": "没有剩余配额"}
    try:
        monkeypatch.setattr("app.services.beike.httpx.AsyncClient", _FakeClient)
        async with SessionLocal() as db:
            try:
                await beike.assess_price(db, None, city="北京市", city_code="110000",
                                         address="某小区", area="90")
                raise AssertionError("应当抛错")
            except AppError as exc:
                assert exc.code == ErrorCode.QUOTA_EXCEEDED
                assert "额度已用完" in exc.message
                assert "贝壳开放平台" in exc.message

        async with SessionLocal() as db:
            cfg = (await db.execute(select(BeikeConfig))).scalars().first()
            assert cfg.last_status == "quota"
            assert "配额" in (cfg.last_error or "")

        st = await _status()
        assert st.status == "connected"
        assert "额度已用完" in st.detail
    finally:
        _FakeClient.assess_payload = {
            "code": 0, "traceId": "t-1",
            "data": {"avgPrice": 47082.88, "priceForHouse": 3121595.22,
                     "avgPriceDim": "resblock", "warningAlert": "", "rowKey": "r-1"},
        }


async def test_beike_assess_rejects_business_error(client, monkeypatch):
    await complete_setup(client)
    await _seed()
    beike._TOKEN_CACHE.clear()
    _FakeClient.assess_payload = {"code": 50001, "msg": "地址异常"}
    try:
        monkeypatch.setattr("app.services.beike.httpx.AsyncClient", _FakeClient)
        async with SessionLocal() as db:
            try:
                await beike.assess_price(db, None, city="北京市", city_code="110000",
                                         address="不存在的小区", area="90")
                raise AssertionError("应当抛错")
            except AppError as exc:
                assert "地址异常" in str(getattr(exc, "message", exc))
    finally:
        _FakeClient.assess_payload = {
            "code": 0, "traceId": "t-1",
            "data": {"avgPrice": 47082.88, "priceForHouse": 3121595.22,
                     "avgPriceDim": "resblock", "warningAlert": "", "rowKey": "r-1"},
        }
