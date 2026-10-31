"""核心配置。

开发 / 测试 / 生产三套环境相互隔离（24 §1.1）。
生产与产品首次启动向导固定使用 MySQL；本地开发与自动化测试默认使用 SQLite
作为"可运行底座"（详见《V2.1 开发前理解报告》§0.3 记录的偏差）。
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="FAM_",
        extra="ignore",
    )

    # ---- 应用 ----
    app_name: str = "财资脉枢"
    app_version: str = "2.1.0"
    api_version: str = "v1"
    app_env: str = "development"  # development | testing | production

    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- 数据库 ----
    # 生产：mysql+aiomysql://user:pass@host:3306/fam?charset=utf8mb4
    # 本地开发/测试：sqlite+aiosqlite:///./data/fam_dev.db
    database_url: str = "sqlite+aiosqlite:///./data/fam_dev.db"
    db_echo: bool = False

    # ---- 会话与令牌 ----
    jwt_secret: str = "dev-insecure-secret-change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    # 刷新令牌有效期：每次刷新都会滑动续期，实际效果是"只要 30 天内用过就不掉线"
    refresh_token_days: int = 30
    # 还款日当天自动提醒（进程内定时检查，不引入外部调度器）
    due_notify_enabled: bool = True
    due_notify_hour: int = 9
    # 登录连续失败触发短暂锁定
    login_max_failures: int = 5
    login_lock_minutes: int = 5

    # ---- 限流与真实 IP（24 §11）----
    # 按来源 IP 的滑动窗口限流：账号锁定防的是「盯一个账号猛试」，
    # 限流防的是「换着用户名挨个试」以及脚本刷接口。
    rate_limit_enabled: bool = True
    rate_limit_login_per_minute: int = 10   # 登录/刷新/解锁：一分钟最多 10 次
    rate_limit_api_per_minute: int = 300    # 其他接口：一分钟最多 300 次
    # 只有直接对端在此范围内时才采信 X-Forwarded-For（nginx 在 Docker 内网）。
    # 默认不含 192.168.0.0/16：局域网机器能直连 18000，不能让它伪造来源。
    trusted_proxies: str = "127.0.0.1,::1,172.16.0.0/12"

    # ---- 密钥加密（24 §3）----
    # Fernet Key（base64 32 字节）。为空时由 jwt_secret 派生（仅限开发）。
    encryption_key: str = ""

    # ---- 附件（24 §4）----
    attachment_dir: str = "./data/attachments"
    attachment_max_mb: int = 20
    backup_dir: str = "./data/backups"

    # ---- 运维 ----
    allow_public_setup_when_uninitialized_only: bool = True

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_testing(self) -> bool:
        return self.app_env == "testing"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def validate_secrets(self) -> None:
        """生产环境密钥自检：缺密钥必须启动失败，不能带着默认值对外服务。"""
        if not self.is_production:
            return
        if (
            not self.jwt_secret
            or self.jwt_secret == "dev-insecure-secret-change-me-in-production"
            or len(self.jwt_secret) < 32
        ):
            raise RuntimeError(
                "生产环境必须设置 FAM_JWT_SECRET（至少 32 位，且不能使用默认值）"
            )
        if not self.encryption_key.strip():
            raise RuntimeError("生产环境必须设置 FAM_ENCRYPTION_KEY（Fernet Key）")
        try:
            from cryptography.fernet import Fernet

            Fernet(self.encryption_key.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"FAM_ENCRYPTION_KEY 不是合法的 Fernet Key：{exc}") from exc

    def ensure_dirs(self) -> None:
        for path in (self.attachment_dir, self.backup_dir):
            os.makedirs(path, exist_ok=True)
        # SQLite 文件目录
        if self.database_url.startswith("sqlite"):
            raw = self.database_url.split("///", 1)[-1]
            directory = os.path.dirname(raw)
            if directory:
                os.makedirs(directory, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


settings = get_settings()
