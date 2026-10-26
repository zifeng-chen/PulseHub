#!/usr/bin/env bash
# FAM V2.1 后端容器入口
# 启动顺序：检查必需环境变量 → 执行数据库迁移 → 校验迁移结果 → 启动业务入口
# 迁移失败必须停止，不允许以半升级状态对外提供服务。
set -euo pipefail

echo "[FAM] 环境：${FAM_APP_ENV:-development}"
echo "[FAM] 数据库：$(python - <<'PY'
import os
url = os.environ.get("FAM_DATABASE_URL", "")
# 隐去口令后再打印
if "@" in url:
    head, tail = url.split("@", 1)
    scheme = head.split("://", 1)[0]
    print(f"{scheme}://***@{tail}")
else:
    print(url)
PY
)"

if [ "${FAM_APP_ENV:-development}" = "production" ]; then
  if [ -z "${FAM_JWT_SECRET:-}" ] || [ "${FAM_JWT_SECRET}" = "dev-insecure-secret-change-me-in-production" ]; then
    echo "[FAM][错误] 生产环境必须设置 FAM_JWT_SECRET（不可使用默认值）" >&2
    exit 1
  fi
  if [ -z "${FAM_ENCRYPTION_KEY:-}" ]; then
    echo "[FAM][错误] 生产环境必须设置 FAM_ENCRYPTION_KEY（Fernet Key）" >&2
    exit 1
  fi
fi

echo "[FAM] 检查数据库连接与迁移版本…"
python - <<'PY'
import asyncio, os, sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

url = os.environ["FAM_DATABASE_URL"]

async def main() -> int:
    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 10} if url.startswith("mysql") else {})
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SELECT VERSION()"))).scalar_one()
            print(f"[FAM] 数据库连接正常，版本：{version}")
    except Exception as exc:  # noqa: BLE001
        print(f"[FAM][错误] 数据库连接失败：{exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    return 0

sys.exit(asyncio.run(main()))
PY

echo "[FAM] 执行数据库迁移 alembic upgrade head …"
alembic upgrade head

echo "[FAM] 校验当前迁移版本…"
alembic current

echo "[FAM] 迁移完成，启动业务入口：$*"
exec "$@"
