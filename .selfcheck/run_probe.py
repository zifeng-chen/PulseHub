"""把 .selfcheck/probe_workbench.py 送进 NAS 容器执行，取回真实接口数据。"""

from __future__ import annotations

import base64
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "deploy"))
import nas_deploy as nd  # noqa: E402

PROBE = pathlib.Path(__file__).resolve().parent / "probe_workbench.py"


def main() -> int:
    payload = base64.b64encode(PROBE.read_bytes()).decode()
    nd.SECRETS = nd.load_secrets()
    nd.SUDO_PASSWORD = nd.SECRETS.get("SSH_PASS")
    client, _user = nd.connect()
    try:
        cmd = (
            f"echo {payload} | base64 -d > /tmp/probe_workbench.py && "
            "docker cp /tmp/probe_workbench.py fam-v21-api:/tmp/probe_workbench.py && "
            "docker exec -e PYTHONPATH=/app fam-v21-api python /tmp/probe_workbench.py"
        )
        _code, out, err = nd.run(client, cmd, sudo=True, timeout=180, quiet=True)
        print(out)
        if err.strip():
            print(err, file=sys.stderr)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
