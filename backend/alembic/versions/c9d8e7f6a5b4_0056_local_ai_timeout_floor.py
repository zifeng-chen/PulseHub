"""0056 本地 AI 超时下限提升到 240 秒

本地 4B 模型（iGPU）实测生成约 7.5 tok/s、提示词预填约 50 tok/s，冷加载还要约 50 秒，
一次问答 30~120 秒是常态。旧默认 60 秒（甚至用户手动设的更低值）会把「还在正常算」的
请求判成超时，配合前端/nginx 的短超时，2026-11-05 早上表现为「调用失败：网络异常」。

超时链路按「前端 axios 330s > nginx proxy_read_timeout 300s > 后端 240s」逐层放宽，
这里把库里所有低于 240 的配置抬到 240，作为兜底安全值。用户仍可在设置页手动改高。

Revision ID: c9d8e7f6a5b4
Revises: e0f1a2b3c4d5
Create Date: 2026-11-05
"""

from __future__ import annotations

from alembic import op

revision = "c9d8e7f6a5b4"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None

TABLE = "local_ai_configs"


def upgrade() -> None:
    op.execute(f"UPDATE {TABLE} SET timeout = 240 WHERE timeout < 240")


def downgrade() -> None:
    # 无法还原用户原先的具体值，降级为无操作（安全值可保留）
    pass
