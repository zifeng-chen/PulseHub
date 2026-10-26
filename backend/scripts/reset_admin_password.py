"""离线重置管理员密码（服务器本机执行，不经 Web 界面）。

为什么需要它：
线上「忘记密码」只有在管理员配置了恢复邮箱时才能重置；未配置恢复邮箱时，
任何线上重置入口都等于「知道用户名就能改密码」。此时必须在服务器本机
（能访问数据库的人）执行本脚本。

用法：
    python -m scripts.reset_admin_password --username admin
    FAM_NEW_PASSWORD='...' python -m scripts.reset_admin_password --username admin --yes
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import select

from app.core.security import assess_password_strength, hash_password
from app.db.base import SessionLocal
from app.db.models.identity import User, UserSession
from app.services import auth as auth_service


async def _reset(username: str, password: str) -> int:
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == username))).scalars().first()
        if user is None:
            print(f"账号不存在：{username}")
            return 1
        if not user.is_system_admin:
            print(f"{username} 不是系统管理员，请改用管理员账号。")
            return 1
        strength = assess_password_strength(password)
        if not strength["acceptable"]:
            print("密码强度不足：" + "；".join(strength["warnings"]))
            return 1

        user.password_hash = hash_password(password)
        user.failed_login_count = 0
        user.locked_until = None
        revoked = await auth_service.revoke_all_sessions(db, user.id)
        await db.commit()
        print(f"已重置 {username} 的密码，并撤销 {revoked} 个会话。")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="离线重置管理员密码")
    parser.add_argument("--username", required=True, help="管理员用户名")
    parser.add_argument("--password", default=None, help="新密码；不填则交互输入")
    parser.add_argument("--yes", action="store_true", help="确认执行")
    args = parser.parse_args()

    password = args.password or os.environ.get("FAM_NEW_PASSWORD") or ""
    if not password:
        password = getpass.getpass("新密码：")
        if password != getpass.getpass("再输一次："):
            print("两次输入的密码不一致。")
            return 1
    if not args.yes and not os.environ.get("FAM_NEW_PASSWORD"):
        print("即将重置管理员密码并使其所有会话失效。加 --yes 确认执行。")
        return 1
    return asyncio.run(_reset(args.username, password))


if __name__ == "__main__":
    sys.exit(main())
