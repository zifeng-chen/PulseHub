"""OCR / 通知的测试与状态必须是真实的。

要求：
- 「测试」要真的调上游（OCR 用内置图片真实识别；通知真的发一条消息）；
- 连接状态不能只看「配置了没有」，而是显示最近一次真实调用（测试 / 识别 / 发送）的结果；
- 每次识别或发送都会写回状态。

本文件覆盖状态语义与写回逻辑（不依赖外网）。
"""

from __future__ import annotations

from sqlalchemy import select

from app.adapters.providers import _persist_status
from app.core.crypto import encrypt_secret
from app.db.base import SessionLocal
from app.db.models.config import NotificationChannel, OcrConfig
from tests.conftest import auth, complete_setup


async def _seed_ocr(last_status: str = "configured", enabled: bool = True,
                    last_error: str | None = None) -> None:
    async with SessionLocal() as db:
        db.add(OcrConfig(
            id="cfg-ocr-test", provider="baidu",
            api_key_cipher=encrypt_secret("a" * 24),
            secret_key_cipher=encrypt_secret("b" * 32),
            enabled=enabled, last_status=last_status, last_error=last_error,
        ))
        await db.commit()


async def _seed_channel(last_status: str = "configured", enabled: bool = True,
                        last_error: str | None = None) -> None:
    async with SessionLocal() as db:
        db.add(NotificationChannel(
            id="cfg-push-test", channel_type="pushplus",
            token_cipher=encrypt_secret("c" * 32),
            enabled=enabled, last_status=last_status, last_error=last_error,
        ))
        await db.commit()


async def test_ocr_configured_but_never_called_is_untested(client):
    """配置并启用但从未真实识别 → 未测试，不能显示「已连接」。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    await _seed_ocr()

    resp = await client.get("/api/v1/settings/ocr", headers=h)
    adapter = resp.json()["data"]["adapter"]
    assert adapter["status"] == "untested", adapter
    assert adapter["status_label"] == "未测试"
    assert "测试识别" in adapter["detail"]


async def test_ocr_status_reflects_last_real_failure(client):
    """上一次真实识别失败 → 状态失败，并带出上游原因。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    await _seed_ocr(last_status="failed", last_error="凭证校验失败：invalid_client")

    resp = await client.get("/api/v1/settings/ocr", headers=h)
    adapter = resp.json()["data"]["adapter"]
    assert adapter["status"] == "failed", adapter
    assert "invalid_client" in adapter["detail"]


async def test_notification_status_reflects_last_real_send(client):
    """通知状态跟随最近一次真实发送结果。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    await _seed_channel()

    resp = await client.get("/api/v1/settings/notifications", headers=h)
    adapter = resp.json()["data"]["adapter"]
    assert adapter["status"] == "untested", adapter

    async with SessionLocal() as db:
        channel = (await db.execute(select(NotificationChannel))).scalars().first()
        channel.last_status = "ok"
        await db.commit()

    resp = await client.get("/api/v1/settings/notifications", headers=h)
    adapter = resp.json()["data"]["adapter"]
    assert adapter["status"] == "connected", adapter
    assert "发送成功" in adapter["detail"]


async def test_persist_status_writes_back_on_every_call(client):
    """每次识别 / 发送后的写回：成功置 ok，失败保留原因。"""
    tokens = await complete_setup(client)
    await _seed_ocr()
    await _seed_channel()

    await _persist_status("ocr", False, "识别失败：OCR 识别失败：HTTP 500")
    await _persist_status("notification", True, "发送成功")

    async with SessionLocal() as db:
        ocr = (await db.execute(select(OcrConfig))).scalars().first()
        channel = (await db.execute(select(NotificationChannel))).scalars().first()
        assert ocr.last_status == "failed"
        assert "HTTP 500" in (ocr.last_error or "")
        assert ocr.last_call_at is not None
        assert channel.last_status == "ok"
        assert channel.last_error is None
        assert channel.last_sent_at is not None


class _FakeResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """模拟百度：换 token 成功，OCR 返回给定 words_result。"""

    words: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, params=None, data=None, json=None):
        if "oauth" in url:
            return _FakeResp(200, {"access_token": "fake-token"})
        return _FakeResp(200, {"words_result": [{"words": w} for w in self.words]})


async def test_ocr_test_uses_real_recognition_and_marks_connected(client, monkeypatch):
    """测试必须真的走识别接口：拿到文字才算成功，并把结果写回状态。"""
    from app.adapters.providers import BaiduOcrAdapter

    tokens = await complete_setup(client)
    await _seed_ocr(last_status="configured")
    _FakeClient.words = ["财资脉枢 OCR 测试", "PulseHub TEST 2026"]
    monkeypatch.setattr("app.adapters.providers.httpx.AsyncClient", _FakeClient)

    async with SessionLocal() as db:
        status = await BaiduOcrAdapter(db).test()

    assert status.status == "connected", status
    assert "首行" in status.detail and "财资脉枢" in status.detail

    async with SessionLocal() as db:
        cfg = (await db.execute(select(OcrConfig))).scalars().first()
        assert cfg.last_status == "ok"
        assert cfg.last_error is None


async def test_ocr_test_empty_recognition_is_failure(client, monkeypatch):
    """凭证有效但识别不出文字，也不能算成功（真实测试的意义）。"""
    from app.adapters.providers import BaiduOcrAdapter

    tokens = await complete_setup(client)
    await _seed_ocr(last_status="configured")
    _FakeClient.words = []
    monkeypatch.setattr("app.adapters.providers.httpx.AsyncClient", _FakeClient)

    async with SessionLocal() as db:
        status = await BaiduOcrAdapter(db).test()

    assert status.status == "failed", status
    assert "空结果" in status.detail

    async with SessionLocal() as db:
        cfg = (await db.execute(select(OcrConfig))).scalars().first()
        assert cfg.last_status == "failed"
        assert "空结果" in (cfg.last_error or "")
