#!/usr/bin/env bash
# PulseHub 2.1 后端容器入口
# 启动顺序：检查必需环境变量 → 执行数据库迁移 → 校验迁移结果 → 启动业务入口
# 迁移失败必须停止，不允许以半升级状态对外提供服务。
set -euo pipefail

echo "[PulseHub] 环境：${PULSEHUB_APP_ENV:-development}"
echo "[PulseHub] 数据库：$(python - <<'PY'
import os
url = os.environ.get("PULSEHUB_DATABASE_URL", "")
# 隐去口令后再打印
if "@" in url:
    head, tail = url.split("@", 1)
    scheme = head.split("://", 1)[0]
    print(f"{scheme}://***@{tail}")
else:
    print(url)
PY
)"

if [ "${PULSEHUB_APP_ENV:-development}" = "production" ]; then
  if [ -z "${PULSEHUB_JWT_SECRET:-}" ] || [ "${PULSEHUB_JWT_SECRET}" = "dev-insecure-secret-change-me-in-production" ]; then
    echo "[PulseHub][错误] 生产环境必须设置 PULSEHUB_JWT_SECRET（不可使用默认值）" >&2
    exit 1
  fi
  if [ -z "${PULSEHUB_ENCRYPTION_KEY:-}" ]; then
    echo "[PulseHub][错误] 生产环境必须设置 PULSEHUB_ENCRYPTION_KEY（Fernet Key）" >&2
    exit 1
  fi
fi

echo "[PulseHub] 检查数据库连接与迁移版本…"
python - <<'PY'
import asyncio, os, sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

url = os.environ["PULSEHUB_DATABASE_URL"]

async def main() -> int:
    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 10} if url.startswith("mysql") else {})
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SELECT VERSION()"))).scalar_one()
            print(f"[PulseHub] 数据库连接正常，版本：{version}")
    except Exception as exc:  # noqa: BLE001
        print(f"[PulseHub][错误] 数据库连接失败：{exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    return 0

sys.exit(asyncio.run(main()))
PY

echo "[PulseHub] 执行数据库迁移 alembic upgrade head …"
alembic upgrade head

echo "[PulseHub] 校验当前迁移版本…"
alembic current

echo "[PulseHub] 迁移完成，启动业务入口：$*"
exec "$@"
