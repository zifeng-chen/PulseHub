"""在 NAS 容器里定位 app 包路径（一次性排查）。"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "deploy"))
import nas_deploy as nd  # noqa: E402


def main() -> int:
    nd.SECRETS = nd.load_secrets()
    nd.SUDO_PASSWORD = nd.SECRETS.get("SSH_PASS")
    client, _ = nd.connect()
    try:
        cmd = (
            "docker exec fam-v21-api sh -lc 'pwd; ls /; "
            "ls /app 2>/dev/null | head; "
            "python -c \"import sys; print(sys.path[:4])\"'"
        )
        _code, out, err = nd.run(client, cmd, sudo=True, timeout=90, quiet=True)
        print(out)
        if err.strip():
            print(err, file=sys.stderr)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
