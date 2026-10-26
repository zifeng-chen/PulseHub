"""本机 → NAS 反向 SSH 隧道（remote port forward）。

把 NAS 的 0.0.0.0:11434 反向映射到本机 127.0.0.1:11434（Ollama）。
使用 paramiko 的 request_port_forward(address, port, handler) 正确实现 -R。
"""
from __future__ import annotations

import importlib.util
import pathlib
import select
import socket
import sys
import threading

import paramiko

_ND = pathlib.Path(__file__).parent / "nas_deploy.py"
_spec = importlib.util.spec_from_file_location("nd", str(_ND))
_nd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_nd)

# 主机与用户名沿用 nas_deploy 的解析规则（FAM_NAS_HOST / deploy/.secrets.local）
_nd.SECRETS.update(_nd.load_secrets())
HOST = _nd.nas_host()
PORT = 22
USER = _nd.SECRETS.get("SSH_USER") or _nd.DEFAULT_SSH_USER
REMOTE_BIND = "0.0.0.0"
REMOTE_PORT = 11434
LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 11434


def _bridge(chan: paramiko.Channel, host: str, port: int) -> None:
    sock = socket.socket()
    try:
        sock.settimeout(15)
        sock.connect((host, port))
    except OSError as e:
        print(f"[bridge] 连接本机失败: {e}", flush=True)
        chan.close()
        return
    try:
        while True:
            r, _w, _x = select.select([sock, chan], [], [], 120)
            if sock in r:
                data = sock.recv(65536)
                if not data:
                    break
                chan.sendall(data)
            if chan in r:
                data = chan.recv(65536)
                if not data:
                    break
                sock.sendall(data)
    except OSError:
        pass
    finally:
        try:
            chan.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            sock.close()
        except Exception:  # noqa: BLE001
            pass


def _handler(channel, src_addr_port, dest_addr_port):
    print(f"[forwarded-tcpip] src={src_addr_port} dest={dest_addr_port}", flush=True)
    threading.Thread(target=_bridge, args=(channel, LOCAL_HOST, LOCAL_PORT), daemon=True).start()


def main() -> int:
    _nd.SECRETS = _nd.load_secrets()
    password = _nd.SECRETS.get("SSH_PASS", "")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, PORT, USER, password, timeout=20, banner_timeout=20)
    transport = client.get_transport()

    # 正确实现 reverse forward：request_port_forward(address, port, handler)
    transport.request_port_forward(REMOTE_BIND, REMOTE_PORT, _handler)
    print(f"[隧道] NAS {REMOTE_BIND}:{REMOTE_PORT} -> 本机 {LOCAL_HOST}:{LOCAL_PORT}", flush=True)

    try:
        while transport.is_active():
            import time
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        print("[隧道] 退出", flush=True)
        transport.cancel_port_forward(REMOTE_BIND, REMOTE_PORT)
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
