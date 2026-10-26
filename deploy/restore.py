#!/usr/bin/env python
"""NAS 离线数据恢复入口（宿主机执行）。

为什么不在网页上做一键恢复：恢复会整库覆盖，属于灾难操作。
这里把它放在运维脚本里，带 dry-run、显式 --yes，并自动停站/重启，避免恢复期间还有写入。

用法：
    python deploy/restore.py --list
    python deploy/restore.py --backup BK20261017120000          # dry-run，只看影响范围
    python deploy/restore.py --backup BK20261017120000 --yes    # 真正执行
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import nas_deploy as nd  # noqa: E402

API_CONTAINER = "fam-v21-api"
WEB_CONTAINER = "fam-v21-web"


def main() -> int:
    parser = argparse.ArgumentParser(description="FAM 离线数据恢复（NAS）")
    parser.add_argument("--list", action="store_true", help="列出可用备份")
    parser.add_argument("--create", action="store_true", help="创建一份新备份")
    parser.add_argument("--backup", help="备份编号或 ID")
    parser.add_argument("--yes", action="store_true", help="真正执行恢复")
    args = parser.parse_args()

    if not args.list and not args.create and not args.backup:
        parser.print_help()
        return 2

    nd.SECRETS = nd.load_secrets()
    client, method = nd.connect()
    nd.log(f"已连接 {nd.SECRETS.get('SSH_USER', nd.DEFAULT_SSH_USER)}@{nd.HOST}（认证方式：{method}）")

    try:
        if args.list:
            code, out, err = nd.run(
                client,
                f"docker exec {API_CONTAINER} python -m scripts.restore_backup --list",
                quiet=True, check=False,
            )
            print((out or err).strip())
            return 0 if code == 0 else 1

        if args.create:
            nd.log("创建备份（含数据库导出与附件快照）…")
            code, out, err = nd.run(
                client,
                f"docker exec {API_CONTAINER} python -m scripts.restore_backup --create",
                quiet=False, check=False,
            )
            return 0 if code == 0 else 1

        if not args.yes:
            code, out, err = nd.run(
                client,
                f"docker exec {API_CONTAINER} python -m scripts.restore_backup --backup {args.backup}",
                quiet=True, check=False,
            )
            print((out or err).strip())
            return 0 if code == 0 else 1

        # 真正执行：先停站，避免恢复期间仍有写入
        nd.log("停对外服务（web），开始恢复…")
        nd.run(client, f"docker stop {WEB_CONTAINER}", quiet=True, check=False)
        try:
            code, out, err = nd.run(
                client,
                f"docker exec {API_CONTAINER} python -m scripts.restore_backup "
                f"--backup {args.backup} --yes",
                quiet=False, check=False,
            )
            ok = code == 0 and "恢复完成" in (out or "")
        finally:
            nd.log("重启 API 与对外服务…")
            nd.run(client, f"docker restart {API_CONTAINER}", quiet=True, check=False)
            nd.run(client, f"docker start {WEB_CONTAINER}", quiet=True, check=False)

        if not ok:
            nd.log("恢复失败，服务已重启；请查看上面的输出并确认数据状态。")
            return 1
        nd.log("恢复完成。所有用户需要重新登录。")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
