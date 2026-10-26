"""把 NAS 上的 FAM 对接本机 Ollama（本地 AI）。

前提：本机 Ollama 已安装、监听 0.0.0.0:11434、已拉取模型、防火墙放行 NAS 来源。

本脚本完成：
  1) 在 NAS MySQL 的 local_ai_configs 表 upsert 一行（base_url/model/protocol/enabled）
  2) 从容器内 curl 本机 Ollama /api/tags，验证跨机可达
  3) 调用 FAM /api/v1/ai/providers/local_ai 状态（需登录，故改为容器内直连验证）

用法：
  python deploy/link_ollama.py --host-ip <本机在局域网中的地址> --model qwen3:4b
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import pathlib
import secrets
import sys

# 复用 nas_deploy.py 的连接与 run 工具
_DEPLOY = pathlib.Path(__file__).parent / "nas_deploy.py"
_spec = importlib.util.spec_from_file_location("nd", str(_DEPLOY))
_nd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_nd)


def _mysql_pw(client) -> str:
    _code, env_out, _err = _nd.run(
        client, "docker inspect mysql-latest --format '{{range .Config.Env}}{{println .}}{{end}}'",
        quiet=True, check=False)
    for line in env_out.splitlines():
        if line.startswith(("MYSQL_ROOT_PASSWORD=", "MARIADB_ROOT_PASSWORD=")):
            return line.split("=", 1)[1]
    return ""


def _household_id(client, pw: str) -> str | None:
    code, out, _err = _nd.run(
        client,
        f"docker exec mysql-latest mysql -uroot -p'{pw}' -N -e "
        f"\"SELECT id FROM fam.households ORDER BY created_at LIMIT 1;\" 2>/dev/null",
        quiet=True, check=False)
    return (out or "").strip() or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-ip", default=os.environ.get("FAM_DEV_HOST", "127.0.0.1"))
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--port", default="11434")
    args = parser.parse_args()

    base_url = args.base_url or f"http://{args.host_ip}:{args.port}/v1"
    _nd.SECRETS = _nd.load_secrets()
    client, _m = _nd.connect()
    pw = _mysql_pw(client)
    hid = _household_id(client, pw)

    row_id = secrets.token_hex(16)  # 32 位 hex，与 new_id() 一致
    hid_sql = f"'{hid}'" if hid else "NULL"
    upsert = (
        f"INSERT INTO fam.local_ai_configs "
        f"(id, household_id, protocol, base_url, model, api_key_cipher, timeout, "
        f" context_length, stream, enabled, last_status) "
        f"VALUES ('{row_id}', {hid_sql}, 'openai_compatible', '{base_url}', '{args.model}', "
        f" NULL, 60, 8192, 1, 1, 'configured') "
        f"ON DUPLICATE KEY UPDATE base_url='{base_url}', model='{args.model}', "
        f" protocol='openai_compatible', enabled=1, last_status='configured';"
    )
    print(f"[1/3] 写入 local_ai_configs（base_url={base_url}, model={args.model}）")
    _nd.run(client, f"docker exec mysql-latest mysql -uroot -p'{pw}' -e \"{upsert}\" fam 2>/dev/null",
            quiet=True, check=False)

    # 核对
    code, out, _err = _nd.run(
        client,
        f"docker exec mysql-latest mysql -uroot -p'{pw}' -N -e "
        f"\"SELECT protocol, base_url, model, enabled FROM fam.local_ai_configs LIMIT 1;\" 2>/dev/null",
        quiet=True, check=False)
    print(f"      核对：{(out or '').strip()}")

    # 容器内验证可达本机 Ollama
    print(f"[2/3] 从 fam-v21-api 容器内验证能访问本机 Ollama ...")
    code, out, err = _nd.run(
        client,
        f"docker exec fam-v21-api python3 -c \"import urllib.request;"
        f"print(urllib.request.urlopen('http://{args.host_ip}:{args.port}/api/tags', timeout=10).read()[:120])\""
        f" 2>&1",
        quiet=True, check=False)
    print(f"      容器内探测：{(out or err).strip()[:200]}")

    # 重启 api 容器使配置生效（配置是读库的，无需重启；但保险起见提示）
    print("[3/3] 完成。请到 FAM 设置页「本地 AI」点「连接测试」确认，或在 AI 助手中提问验证。")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
