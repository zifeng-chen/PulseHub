"""密钥加密接口（18 §2.6、24 §3）。

Token / Secret / 数据库口令不得明文落库或落日志；统一使用主加密密钥加密保存，
数据库普通字段只存密文，展示时只给掩码。
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_KEY_VERSION = "v1"


def _derive_key(master: str) -> bytes:
    digest = hashlib.sha256(master.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    key = settings.encryption_key.strip()
    if not key:
        # 仅开发环境：由 jwt_secret 派生；生产必须显式提供 FAM_ENCRYPTION_KEY
        return Fernet(_derive_key(settings.jwt_secret))
    return Fernet(key.encode("utf-8"))


def encrypt_secret(plaintext: str | None) -> str | None:
    if plaintext is None or plaintext == "":
        return None
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None


def mask(plaintext: str | None) -> str:
    """展示用掩码，永不返回明文。"""
    if not plaintext:
        return ""
    if len(plaintext) <= 4:
        return "****"
    return f"{plaintext[:2]}{'*' * (len(plaintext) - 4)}{plaintext[-2:]}"


def mask_cipher(ciphertext: str | None) -> str:
    """对已加密字段做展示掩码（不泄露明文长度）。"""
    if not ciphertext:
        return ""
    return "••••••••"


def key_version() -> str:
    return _KEY_VERSION
