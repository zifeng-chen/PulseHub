"""把指定探针脚本送进 NAS 容器执行（只读）。用法：python run_probe_file.py <脚本名>"""

import base64
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path("deploy").resolve()))
import nas_deploy as nd  # noqa: E402

here = pathlib.Path(__file__).resolve().parent
probe = here / sys.argv[1]


def main() -> int:
    payload = base64.b64encode(probe.read_bytes()).decode()
    nd.SECRETS = nd.load_secrets()
    nd.SUDO_PASSWORD = nd.SECRETS.get("SSH_PASS")
    client, _user = nd.connect()
    try:
        cmd = (
            f"echo {payload} | base64 -d > /tmp/probe_x.py && "
            "docker cp /tmp/probe_x.py fam-v21-api:/tmp/probe_x.py && "
            "docker exec -e PYTHONPATH=/app fam-v21-api python /tmp/probe_x.py"
        )
        _code, out, err = nd.run(client, cmd, sudo=True, timeout=240, quiet=True)
        print(out)
        if err.strip() and "Event loop is closed" not in err:
            print("STDERR:", err[:1500], file=sys.stderr)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
