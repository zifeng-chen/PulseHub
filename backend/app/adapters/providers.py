"""具体能力适配器：本地 AI / 第三方 AI / 百度 OCR / 通知（09、14 §5、08 §5–§8）。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.core.errors import AppError, ErrorCode
from app.core.ids import utc_iso
from app.db.models.config import (
    LocalAIConfig,
    NotificationChannel,
    OcrConfig,
    ThirdPartyAIProvider,
)
from app.adapters.base import AdapterStatus, BaseAdapter

_TIMEOUT = 8.0
# 内置 OCR 测试图片：真实识别校验用（不是只验凭证）
_OCR_TEST_IMAGE = Path(__file__).resolve().parents[1] / "assets" / "ocr_test.png"


def _fmt_ts(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M")


async def _persist_status(kind: str, ok: bool, detail: str) -> None:
    """把最近一次真实调用（测试/识别/发送）的结果写回配置表。

    使用独立会话：即使业务流程随后回滚，连接状态仍然如实保留。
    """
    from sqlalchemy import update

    from app.db.base import SessionLocal

    now = datetime.now(timezone.utc)
    try:
        async with SessionLocal() as session:
            if kind == "ocr":
                await session.execute(update(OcrConfig).values(
                    last_status="ok" if ok else "failed",
                    last_call_at=now,
                    last_error=None if ok else detail[:500],
                ))
            else:
                await session.execute(update(NotificationChannel).values(
                    last_status="ok" if ok else "failed",
                    last_sent_at=now,
                    last_error=None if ok else detail[:500],
                ))
            await session.commit()
    except Exception:  # noqa: BLE001 - 状态写入失败不得影响业务流程
        pass

# 状态探测缓存：避免设置页/每次问答都真的打一次外部服务
_PROBE_CACHE: dict[str, tuple[float, bool, str]] = {}
_PROBE_TTL = 15.0
_PROBE_TIMEOUT = 3.0


async def _probe_endpoint(
    base_url: str, api_key_cipher: str | None, cache_key: str
) -> tuple[bool, str, list[str]]:
    """探测 OpenAI 兼容端点是否真的可达（GET /models）。

    返回 (是否可达, 说明, 可用模型列表)。结果缓存 15 秒，状态页与问答路径共用，
    避免"配置了但服务挂了"被误报为已连接。
    """
    now = time.monotonic()
    cached = _PROBE_CACHE.get(cache_key)
    if cached and now - cached[0] < _PROBE_TTL:
        return cached[1], cached[2], cached[3]

    url = base_url.rstrip("/") + "/models"
    headers: dict[str, str] = {}
    token = decrypt_secret(api_key_cipher) if api_key_cipher else None
    if token:
        headers["Authorization"] = f"Bearer {token}"

    models: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
        ok = resp.status_code < 500
        note = "ok" if ok else f"HTTP {resp.status_code}"
        if ok:
            try:
                payload = resp.json()
                models = [m.get("id") for m in payload.get("data", []) if isinstance(m, dict)]
            except Exception:  # noqa: BLE001 - 有些实现不返回标准 /models
                models = []
    except Exception as exc:  # noqa: BLE001 - 连接拒绝/超时统一归为不可达
        ok, note = False, f"{exc.__class__.__name__}: {str(exc)[:80]}"

    _PROBE_CACHE[cache_key] = (now, ok, note, models)
    return ok, note, models


class LocalAIAdapter(BaseAdapter):
    """本地 AI（OpenAI 兼容 / Ollama）。默认不出网，失败保留确定性页面。"""

    key = "local_ai"
    label = "本地 AI"

    def __init__(self, db: AsyncSession, household_id: str | None = None) -> None:
        self.db = db
        self.household_id = household_id

    async def _config(self) -> LocalAIConfig | None:
        return (await self.db.execute(select(LocalAIConfig))).scalars().first()

    async def status(self, probe: bool = True) -> AdapterStatus:
        cfg = await self._config()
        if cfg is None or not cfg.base_url:
            return AdapterStatus(key=self.key, label=self.label, status="unconfigured",
                                 detail="尚未配置服务地址与模型")
        if not cfg.enabled:
            return AdapterStatus(key=self.key, label=self.label, status="disabled",
                                 detail="已配置但未启用", extra={"model": cfg.model})

        extra = {"protocol": cfg.protocol, "model": cfg.model, "base_url": cfg.base_url}
        address = f"{cfg.base_url} · {cfg.model or '未指定模型'}"

        if not probe:
            return AdapterStatus(key=self.key, label=self.label, status="connected",
                                 detail=address, extra={**extra, "reachable": None})

        ok, note, models = await _probe_endpoint(cfg.base_url, cfg.api_key_cipher,
                                                 f"local|{cfg.base_url}|{cfg.model}")
        if not ok:
            return AdapterStatus(
                key=self.key, label=self.label, status="unreachable",
                detail=f"已配置但连不上：{note}（{cfg.base_url}）",
                last_checked_at=utc_iso(datetime.now(timezone.utc)),
                extra={**extra, "reachable": False},
            )
        # 服务可达但模型没拉取——这种情况光看"端口通"会误判为可用
        if cfg.model and models and cfg.model not in models:
            return AdapterStatus(
                key=self.key, label=self.label, status="unreachable",
                detail=f"服务可达，但模型 {cfg.model} 不存在（可用：{', '.join(models[:3])}）",
                last_checked_at=utc_iso(datetime.now(timezone.utc)),
                extra={**extra, "reachable": True, "model_missing": True, "models": models[:10]},
            )
        return AdapterStatus(
            key=self.key, label=self.label, status="connected", detail=address,
            last_checked_at=utc_iso(datetime.now(timezone.utc)),
            extra={**extra, "reachable": True, "models": models[:10]},
        )

    async def test(self) -> AdapterStatus:
        cfg = await self._config()
        if cfg is None or not cfg.base_url:
            raise AppError(ErrorCode.PROVIDER_NOT_CONFIGURED,
                           "本地 AI 尚未配置，请前往设置完成配置", status_code=409)
        url = cfg.base_url.rstrip("/") + "/models"
        checked = utc_iso(datetime.now(timezone.utc))
        headers = {}
        token = decrypt_secret(cfg.api_key_cipher)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                return AdapterStatus(key=self.key, label=self.label, status="failed",
                                     detail=f"服务返回 {resp.status_code}", last_checked_at=checked)
            models: list[str] = []
            try:
                payload = resp.json()
                models = [m.get("id") for m in payload.get("data", []) if isinstance(m, dict)]
            except ValueError:
                pass
            return AdapterStatus(
                key=self.key, label=self.label, status="connected",
                detail=f"模型 {cfg.model or (models[0] if models else '未指定')}",
                last_checked_at=checked,
                extra={"models": models[:10], "model": cfg.model},
            )
        except httpx.HTTPError as exc:
            return AdapterStatus(key=self.key, label=self.label, status="failed",
                                 detail=f"连接失败：{exc.__class__.__name__}", last_checked_at=checked)

    async def complete(
        self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 512
    ) -> str:
        """OpenAI 兼容 /chat/completions 对话（09 §5.1：本地 AI，默认不出网）。"""
        cfg = await self._config()
        if cfg is None or not cfg.base_url:
            raise AppError(ErrorCode.PROVIDER_NOT_CONFIGURED,
                           "本地 AI 尚未配置，请前往设置完成配置", status_code=409)
        if not cfg.enabled:
            raise AppError(ErrorCode.PROVIDER_NOT_CONFIGURED,
                           "本地 AI 已配置但未启用，请前往设置启用", status_code=409)
        url = cfg.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        token = decrypt_secret(cfg.api_key_cipher)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # think=False：关闭 qwen3 等 reasoning 模型的思考输出，避免 content 被 thinking 占满
        payload = {
            "model": cfg.model or "llama3",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "think": False,
        }
        try:
            async with httpx.AsyncClient(timeout=cfg.timeout or 60.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE,
                           f"本地 AI 连接失败：{exc.__class__.__name__}", status_code=502)
        if resp.status_code >= 400:
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE,
                           f"本地 AI 调用失败：HTTP {resp.status_code}", status_code=502)
        try:
            body = resp.json()
            message = body["choices"][0]["message"]
            content = (message.get("content") or "").strip()
            # qwen3 等 reasoning 模型：若 content 为空，回退到 reasoning（去思考的最终结论）
            if not content and message.get("reasoning"):
                content = str(message["reasoning"]).strip()
            if not content:
                raise ValueError("empty content")
            return content
        except (ValueError, KeyError, IndexError, TypeError):
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE,
                           "本地 AI 返回格式无法解析", status_code=502)


class ThirdPartyAIAdapter(BaseAdapter):
    """第三方 AI API：默认关闭，只有显式启用 + 隐私允许 + 外发预览 + 审计后才可调用。"""

    key = "third_party_ai"
    label = "第三方 AI 接口"

    def __init__(self, db: AsyncSession, household_id: str | None = None) -> None:
        self.db = db
        self.household_id = household_id

    async def _providers(self) -> list[ThirdPartyAIProvider]:
        conditions = [ThirdPartyAIProvider.deleted_at.is_(None)]
        if self.household_id:
            conditions.append(ThirdPartyAIProvider.household_id == self.household_id)
        return list((await self.db.execute(select(ThirdPartyAIProvider).where(*conditions))).scalars().all())

    async def status(self, probe: bool = True) -> AdapterStatus:
        providers = await self._providers()
        if not providers:
            return AdapterStatus(key=self.key, label=self.label, status="unconfigured",
                                 detail="尚未配置任何第三方 AI 接口")
        enabled = [p for p in providers if p.enabled]
        base = {"enabled_count": len(enabled), "total": len(providers)}
        if not enabled:
            return AdapterStatus(key=self.key, label=self.label, status="disabled",
                                 detail=f"共 {len(providers)} 个提供商，均已停用",
                                 extra=base)

        target = enabled[0]
        if not probe or not target.base_url:
            return AdapterStatus(
                key=self.key, label=self.label, status="connected",
                detail=f"共 {len(providers)} 个提供商，已启用 {len(enabled)} 个",
                extra={**base, "reachable": None if probe else True},
            )

        ok, note, _models = await _probe_endpoint(target.base_url, target.api_key_cipher,
                                                  f"third|{target.base_url}")
        if not ok:
            return AdapterStatus(
                key=self.key, label=self.label, status="unreachable",
                detail=f"{target.name} 连不上：{note}",
                last_checked_at=utc_iso(datetime.now(timezone.utc)),
                extra={**base, "reachable": False},
            )
        return AdapterStatus(
            key=self.key, label=self.label, status="connected",
            detail=f"{target.name} 可用 · 已启用 {len(enabled)}/{len(providers)}",
            last_checked_at=utc_iso(datetime.now(timezone.utc)),
            extra={**base, "reachable": True},
        )

    async def test(self) -> AdapterStatus:
        providers = await self._providers()
        if not providers:
            raise AppError(ErrorCode.PROVIDER_NOT_CONFIGURED,
                           "第三方 AI 尚未配置，请前往设置完成配置", status_code=409)
        # 第三方 AI 默认关闭：未启用时测试只返回状态，不发起外发
        enabled = [p for p in providers if p.enabled]
        if not enabled:
            return AdapterStatus(key=self.key, label=self.label, status="disabled",
                                 detail="第三方 AI 默认关闭，未发起任何外部调用")
        target = enabled[0]
        if not target.base_url:
            return AdapterStatus(key=self.key, label=self.label, status="failed",
                                 detail="缺少 Base URL")
        url = target.base_url.rstrip("/") + "/models"
        headers = {}
        token = decrypt_secret(target.api_key_cipher)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        checked = utc_iso(datetime.now(timezone.utc))
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, headers=headers)
            ok = resp.status_code < 400
            return AdapterStatus(
                key=self.key, label=self.label,
                status="connected" if ok else "failed",
                detail=f"{target.name}：HTTP {resp.status_code}",
                last_checked_at=checked,
                extra={"provider": target.name, "egress": True},
            )
        except httpx.HTTPError as exc:
            return AdapterStatus(key=self.key, label=self.label, status="failed",
                                 detail=f"外发失败：{exc.__class__.__name__}", last_checked_at=checked)

    async def complete(
        self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 800
    ) -> tuple[str, str]:
        """OpenAI 兼容第三方 AI 对话（09 §5.1：显式启用 + 隐私允许 + 外发审计后才可调用）。"""
        providers = await self._providers()
        enabled = sorted(
            [p for p in providers if p.enabled and p.base_url],
            key=lambda p: p.priority,
        )
        if not enabled:
            raise AppError(ErrorCode.PROVIDER_NOT_CONFIGURED,
                           "第三方 AI 未启用或未配置，请前往设置完成配置", status_code=409)
        target = enabled[0]
        url = target.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        token = decrypt_secret(target.api_key_cipher)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = {
            "model": target.default_model or "gpt-4o-mini",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=target.timeout or 60.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE,
                           f"第三方 AI 外发失败：{exc.__class__.__name__}", status_code=502)
        if resp.status_code >= 400:
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE,
                           f"第三方 AI 调用失败：HTTP {resp.status_code}", status_code=502)
        try:
            body = resp.json()
            return body["choices"][0]["message"]["content"], target.name
        except (ValueError, KeyError, IndexError, TypeError):
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE,
                           "第三方 AI 返回格式无法解析", status_code=502)


class BaiduOcrAdapter(BaseAdapter):
    """百度 OCR 适配器。失败可手工录入，不影响手工记账。

    测试与状态都是"真"的：
    - test() 用内置测试图片真实调用一次通用文字识别，不只看凭证；
    - 每次识别（成功或失败）都会把结果写回配置，状态页显示最近一次真实结果。
    """

    key = "ocr"
    label = "百度 OCR"

    def __init__(self, db: AsyncSession, household_id: str | None = None) -> None:
        self.db = db
        self.household_id = household_id

    async def _config(self) -> OcrConfig | None:
        return (await self.db.execute(select(OcrConfig))).scalars().first()

    async def status(self) -> AdapterStatus:
        cfg = await self._config()
        if cfg is None or not cfg.api_key_cipher or not cfg.secret_key_cipher:
            return AdapterStatus(key=self.key, label=self.label, status="unconfigured",
                                 detail="尚未配置 API Key / Secret Key")
        if not cfg.enabled:
            return AdapterStatus(key=self.key, label=self.label, status="disabled",
                                 detail="已配置但未启用", extra={"region": cfg.region})

        checked = _fmt_ts(cfg.last_call_at)
        extra = {"region": cfg.region, "last_status": cfg.last_status,
                 "last_error": cfg.last_error}
        if cfg.last_status == "failed":
            return AdapterStatus(
                key=self.key, label=self.label, status="failed",
                detail=f"最近一次识别失败：{cfg.last_error or '未知原因'}"
                       + (f"（{checked}）" if checked else ""),
                last_checked_at=utc_iso(cfg.last_call_at) if cfg.last_call_at else None,
                extra=extra,
            )
        if cfg.last_status == "ok":
            return AdapterStatus(
                key=self.key, label=self.label, status="connected",
                detail="最近一次识别成功" + (f"（{checked}）" if checked else ""),
                last_checked_at=utc_iso(cfg.last_call_at) if cfg.last_call_at else None,
                extra=extra,
            )
        return AdapterStatus(
            key=self.key, label=self.label, status="untested",
            detail="已配置并启用，但还没有真实识别记录；点「测试识别」用内置图片验证",
            extra=extra,
        )

    async def test(self) -> AdapterStatus:
        """真实测试：凭证换 token → 用内置测试图片调用通用文字识别 → 校验识别结果。"""
        import base64

        cfg = await self._config()
        if cfg is None or not cfg.api_key_cipher or not cfg.secret_key_cipher:
            raise AppError(ErrorCode.PROVIDER_NOT_CONFIGURED,
                           "百度 OCR 尚未配置，请前往设置完成配置", status_code=409)
        key = decrypt_secret(cfg.api_key_cipher)
        secret = decrypt_secret(cfg.secret_key_cipher)
        checked = utc_iso(datetime.now(timezone.utc))

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                token_resp = await client.post(
                    "https://aip.baidubce.com/oauth/2.0/token",
                    params={"grant_type": "client_credentials",
                            "client_id": key, "client_secret": secret},
                )
                try:
                    token_body = token_resp.json()
                except ValueError:
                    token_body = {}
                access_token = token_body.get("access_token")
                if token_resp.status_code >= 400 or not access_token:
                    reason = (token_body.get("error_description") or token_body.get("error")
                              or f"HTTP {token_resp.status_code}")
                    detail = f"凭证校验失败：{reason}（API Key 24 位、Secret Key 32 位，注意不要填反）"
                    await _persist_status("ocr", False, detail)
                    return AdapterStatus(key=self.key, label=self.label, status="failed",
                                         detail=detail, last_checked_at=checked)

                if not _OCR_TEST_IMAGE.exists():
                    detail = "凭证有效，但缺少内置测试图片，未能完成识别校验"
                    await _persist_status("ocr", False, detail)
                    return AdapterStatus(key=self.key, label=self.label, status="failed",
                                         detail=detail, last_checked_at=checked)

                image_b64 = base64.b64encode(_OCR_TEST_IMAGE.read_bytes()).decode()
                ocr_resp = await client.post(
                    "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic",
                    params={"access_token": access_token},
                    data={"image": image_b64, "detect_direction": "false"},
                )
                try:
                    body = ocr_resp.json()
                except ValueError:
                    body = {}
                if ocr_resp.status_code >= 400 or body.get("error_code"):
                    reason = body.get("error_msg") or f"HTTP {ocr_resp.status_code}"
                    detail = f"识别失败：{reason}（凭证有效，但识别接口未返回结果）"
                    await _persist_status("ocr", False, detail)
                    return AdapterStatus(key=self.key, label=self.label, status="failed",
                                         detail=detail, last_checked_at=checked)

                words = [w.get("words", "") for w in body.get("words_result", [])
                         if isinstance(w, dict) and w.get("words")]
                if not words:
                    detail = "识别接口返回空结果：凭证有效但未识别出文字，请检查账号权限"
                    await _persist_status("ocr", False, detail)
                    return AdapterStatus(key=self.key, label=self.label, status="failed",
                                         detail=detail, last_checked_at=checked)

                detail = f"识别成功：{len(words)} 行 · 首行「{words[0][:20]}」"
                await _persist_status("ocr", True, detail)
                return AdapterStatus(key=self.key, label=self.label, status="connected",
                                     detail=detail, last_checked_at=checked)
        except httpx.HTTPError as exc:
            detail = f"连接失败：{exc.__class__.__name__}"
            await _persist_status("ocr", False, detail)
            return AdapterStatus(key=self.key, label=self.label, status="failed",
                                 detail=detail, last_checked_at=checked)

    async def recognize(self, image_path: str) -> str:
        """识别图片为文本（06 §10：OCR 识别 → 结构化）。未配置抛降级错误。"""
        try:
            text = await self._recognize(image_path)
        except Exception as exc:  # noqa: BLE001 - 任何失败都要如实反映到连接状态
            detail = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
            await _persist_status("ocr", False, f"识别失败：{detail}")
            raise
        await _persist_status("ocr", True, "识别成功")
        return text

    async def _recognize(self, image_path: str) -> str:
        import base64

        cfg = await self._config()
        if cfg is None or not cfg.api_key_cipher or not cfg.secret_key_cipher:
            raise AppError(ErrorCode.PROVIDER_NOT_CONFIGURED,
                           "百度 OCR 尚未配置，请前往设置完成配置", status_code=409)
        if not cfg.enabled:
            raise AppError(ErrorCode.PROVIDER_NOT_CONFIGURED,
                           "百度 OCR 已停用，请前往设置启用", status_code=409)

        key = decrypt_secret(cfg.api_key_cipher)
        secret = decrypt_secret(cfg.secret_key_cipher)

        async with httpx.AsyncClient(timeout=30.0) as client:
            token_resp = await client.post(
                "https://aip.baidubce.com/oauth/2.0/token",
                params={"grant_type": "client_credentials",
                        "client_id": key, "client_secret": secret},
            )
            if token_resp.status_code >= 400:
                raise AppError(ErrorCode.SERVICE_UNAVAILABLE,
                               f"OCR 凭证校验失败：HTTP {token_resp.status_code}", status_code=502)
            access_token = token_resp.json().get("access_token")
            if not access_token:
                raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "OCR 凭证校验失败", status_code=502)

            with open(image_path, "rb") as fh:
                image_b64 = base64.b64encode(fh.read()).decode()

            ocr_resp = await client.post(
                "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic",
                params={"access_token": access_token},
                # detect_direction：手机拍的房本常常是横着的，自动转正后识别率明显更高
                data={"image": image_b64, "detect_direction": "true"},
            )
            if ocr_resp.status_code >= 400:
                raise AppError(ErrorCode.SERVICE_UNAVAILABLE,
                               f"OCR 识别失败：HTTP {ocr_resp.status_code}", status_code=502)
            payload = ocr_resp.json()
            if payload.get("error_code"):
                raise AppError(ErrorCode.SERVICE_UNAVAILABLE,
                               f"OCR 识别失败：{payload.get('error_msg') or payload['error_code']}",
                               status_code=502)
            words = [w.get("words", "") for w in payload.get("words_result", []) if isinstance(w, dict)]
            return "\n".join(words)


class PushPlusAdapter(BaseAdapter):
    """PushPlus 通知适配器。通知不是后台任务：同步发送 + 手动重试。

    测试与状态都是"真"的：测试会真的发一条消息；之后每次发送（成功或失败）
    都会把结果写回配置，状态页显示最近一次真实发送结果。
    """

    key = "notification"
    label = "提醒通知"

    def __init__(self, db: AsyncSession, household_id: str | None = None) -> None:
        self.db = db
        self.household_id = household_id

    async def _channel(self) -> NotificationChannel | None:
        return (await self.db.execute(select(NotificationChannel))).scalars().first()

    async def status(self) -> AdapterStatus:
        channel = await self._channel()
        if channel is None or not channel.token_cipher:
            return AdapterStatus(key=self.key, label=self.label, status="unconfigured",
                                 detail="尚未配置 PushPlus Token")
        if not channel.enabled:
            return AdapterStatus(key=self.key, label=self.label, status="disabled",
                                 detail="已配置 PushPlus Token 但未启用")

        checked = _fmt_ts(channel.last_sent_at)
        extra = {"last_status": channel.last_status, "last_error": channel.last_error}
        if channel.last_status == "failed":
            return AdapterStatus(
                key=self.key, label=self.label, status="failed",
                detail=f"最近一次发送失败：{channel.last_error or '未知原因'}"
                       + (f"（{checked}）" if checked else ""),
                last_checked_at=utc_iso(channel.last_sent_at) if channel.last_sent_at else None,
                extra=extra,
            )
        if channel.last_status == "ok":
            return AdapterStatus(
                key=self.key, label=self.label, status="connected",
                detail="最近一次发送成功" + (f"（{checked}）" if checked else ""),
                last_checked_at=utc_iso(channel.last_sent_at) if channel.last_sent_at else None,
                extra=extra,
            )
        return AdapterStatus(
            key=self.key, label=self.label, status="untested",
            detail="已配置并启用，但还没有真实发送记录；点「发送测试通知」验证",
            extra=extra,
        )

    async def test(self) -> AdapterStatus:
        channel = await self._channel()
        if channel is None or not channel.token_cipher:
            raise AppError(ErrorCode.PROVIDER_NOT_CONFIGURED,
                           "提醒通知尚未配置，请前往设置完成配置", status_code=409)
        token = decrypt_secret(channel.token_cipher)
        checked = utc_iso(datetime.now(timezone.utc))
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    "https://www.pushplus.plus/send",
                    json={"token": token, "title": "财资脉枢 测试通知",
                          "content": "这是一条来自财资脉枢（PulseHub）的真实测试消息，收到即代表推送可用。"},
                )
            body = {}
            try:
                body = resp.json()
            except ValueError:
                pass
            ok = resp.status_code < 400 and (body.get("code") in (200, None))
            if ok:
                detail = "测试消息已真实发送，请查看手机通知"
                await _persist_status("notification", True, detail)
                return AdapterStatus(key=self.key, label=self.label, status="connected",
                                     detail=detail, last_checked_at=checked)
            reason = body.get("msg") or body.get("data") or f"HTTP {resp.status_code}"
            detail = f"发送失败：{reason}（PushPlus token 为 32 位字符串，不要带网址或空格）"
            await _persist_status("notification", False, detail)
            return AdapterStatus(key=self.key, label=self.label, status="failed",
                                 detail=detail, last_checked_at=checked)
        except httpx.HTTPError as exc:
            detail = f"发送失败：{exc.__class__.__name__}"
            await _persist_status("notification", False, detail)
            return AdapterStatus(key=self.key, label=self.label, status="failed",
                                 detail=detail, last_checked_at=checked)

    async def send(self, title: str, content: str) -> None:
        """实际发送 PushPlus 通知（08 §12：同步发送，结果写通知记录，失败可重试）。"""
        channel = await self._channel()
        if channel is None or not channel.token_cipher:
            raise AppError(ErrorCode.PROVIDER_NOT_CONFIGURED,
                           "提醒通知尚未配置，请前往设置完成配置", status_code=409)
        if not channel.enabled:
            raise AppError(ErrorCode.PROVIDER_NOT_CONFIGURED,
                           "提醒通知已停用，请前往设置启用", status_code=409)
        token = decrypt_secret(channel.token_cipher)
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    "https://www.pushplus.plus/send",
                    json={"token": token, "title": title, "content": content, "template": "txt"},
                )
        except httpx.HTTPError as exc:
            detail = f"发送失败：{exc.__class__.__name__}"
            await _persist_status("notification", False, detail)
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE,
                           f"PushPlus 发送失败：{exc.__class__.__name__}", status_code=502)
        body = {}
        try:
            body = resp.json()
        except ValueError:
            pass
        ok = resp.status_code < 400 and (body.get("code") in (200, None))
        if not ok:
            detail = f"发送失败：HTTP {resp.status_code} {body.get('msg') or ''}".strip()
            await _persist_status("notification", False, detail)
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE,
                           f"PushPlus 发送失败：HTTP {resp.status_code} {body.get('msg') or ''}",
                           status_code=502)
        await _persist_status("notification", True, "发送成功")


def build_adapters(db: AsyncSession, household_id: str | None = None) -> dict[str, BaseAdapter]:
    return {
        "local_ai": LocalAIAdapter(db, household_id),
        "third_party_ai": ThirdPartyAIAdapter(db, household_id),
        "ocr": BaiduOcrAdapter(db, household_id),
        "notification": PushPlusAdapter(db, household_id),
    }
