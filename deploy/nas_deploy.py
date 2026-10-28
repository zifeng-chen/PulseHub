#!/usr/bin/env python
"""FAM V2.1 —— NAS Docker 一键部署脚本。

用法（本机执行）：
    python deploy/nas_deploy.py --step probe        # 探测环境与权限
    python deploy/nas_deploy.py --step mysql        # 建库（不改其他库）
    python deploy/nas_deploy.py --step upload       # 上传工程包
    python deploy/nas_deploy.py --step build        # 构建镜像
    python deploy/nas_deploy.py --step up           # 起容器
    python deploy/nas_deploy.py --step verify       # 验健康与接口
    python deploy/nas_deploy.py --step e2e          # 只跑本地全链路 E2E 门禁
    python deploy/nas_deploy.py --step all          # 全流程（默认先跑 E2E，失败即中止）

认证与权限：
- 优先使用本机私钥 ~/.ssh/fam_v21_nas 免密登录；
- 私钥不可用时回退到密码登录，并自动把公钥安装到 NAS 的 authorized_keys；
- sudo 通过 `sudo -S` 从标准输入喂入密码，因此**不需要修改 NAS 的 sudoers**。

安全约束：
- 凭据从 deploy/.secrets.local 读取，不回显、不写入日志、不进部署包；
- 不改动 NAS 上既有数据库（只创建/复用 fam 库）；
- 迁移失败即退出，不启动半升级系统。
"""

from __future__ import annotations

import argparse
import base64
import os
import pathlib
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse

import paramiko
from sqlalchemy.engine import make_url

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
# 目标主机不写死在代码里：优先环境变量 FAM_NAS_HOST，其次 deploy/.secrets.local 的 NAS_HOST。
# 这样仓库可以公开，而每台机器用自己的私有凭据文件填真实地址。
SSH_PORT = 22
DEFAULT_SSH_USER = os.environ.get("FAM_NAS_USER", "nasadmin")
KEY_PATH = pathlib.Path.home() / ".ssh" / "fam_v21_nas"
PUB_KEY_PATH = pathlib.Path(str(KEY_PATH) + ".pub")
KEY_TAG = "fam-v21-deploy@workbuddy"

LOCAL_ROOT = pathlib.Path(__file__).resolve().parent.parent
SECRETS_FILE = LOCAL_ROOT / "deploy" / ".secrets.local"

REMOTE_DIR = "/volume1/docker/fam-v21"
DB_NAME = "fam"
# 应用侧不使用 root：创建仅授权 fam 库的专用账号（最小权限）
DB_APP_USER = "fam_app"
# 允许来源：Docker 默认地址池（172.17-31）与 10.x 私网。
# 实测应用容器经宿主 DNAT 后，MySQL 看到的来源是 Docker 网关 172.17.0.1。
DB_APP_HOSTS = ("172.%", "10.%")

EXCLUDE_DIRS = {
    ".git", ".workbuddy", "node_modules", "dist", ".vite", "__pycache__",
    ".pytest_cache", "data", ".venv", "venv", ".idea", ".vscode",
}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".log", ".db", ".tar.gz", ".zip"}
EXCLUDE_FILES = {".env", "fam-v21.env", ".secrets.local"}

SECRETS: dict[str, str] = {}
SUDO_PASSWORD: str | None = None
REMOTE_UID: int | None = None
REMOTE_GID: int | None = None


def log(msg: str) -> None:
    print(f"[部署] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[部署][警告] {msg}", flush=True)


def load_secrets() -> dict[str, str]:
    data: dict[str, str] = {}
    if not SECRETS_FILE.exists():
        warn(f"未找到凭据文件 {SECRETS_FILE}，将只尝试密钥登录。")
        return data
    for raw in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    log(f"已加载本机凭据文件（{len(data)} 项，内容不回显）")
    return data


def nas_host() -> str:
    """目标主机：环境变量 FAM_NAS_HOST 优先，其次凭据文件的 NAS_HOST。"""
    host = os.environ.get("FAM_NAS_HOST") or SECRETS.get("NAS_HOST") or ""
    if not host:
        raise SystemExit(
            "未配置目标主机：请设置环境变量 FAM_NAS_HOST，"
            "或在 deploy/.secrets.local 中写入 NAS_HOST=<你的 NAS 地址>。"
        )
    return host


# ---------------- 连接 ----------------

def connect() -> tuple[paramiko.SSHClient, str]:
    user = SECRETS.get("SSH_USER") or DEFAULT_SSH_USER
    host = nas_host()
    last_error: Exception | None = None

    if KEY_PATH.exists():
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=host, port=SSH_PORT, username=user,
                key_filename=str(KEY_PATH), timeout=20,
                banner_timeout=30, auth_timeout=30, allow_agent=False, look_for_keys=False,
            )
            return client, "密钥"
        except paramiko.AuthenticationException as exc:
            last_error = exc
            client.close()

    password = SECRETS.get("SSH_PASS")
    if password:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=host, port=SSH_PORT, username=user, password=password,
                timeout=20, banner_timeout=30, auth_timeout=30,
                allow_agent=False, look_for_keys=False,
            )
            return client, "密码"
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            client.close()

    raise RuntimeError(f"SSH 认证失败：{type(last_error).__name__ if last_error else '无可用凭据'}")


def install_public_key(client: paramiko.SSHClient) -> None:
    """把本机公钥写入 NAS，后续即可免密登录。已存在则跳过。"""
    if not PUB_KEY_PATH.exists():
        warn("未找到公钥文件，跳过安装。")
        return
    pub = PUB_KEY_PATH.read_text(encoding="utf-8").strip()
    cmd = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"touch ~/.ssh/authorized_keys && "
        f"grep -q '{KEY_TAG}' ~/.ssh/authorized_keys 2>/dev/null || echo '{pub}' >> ~/.ssh/authorized_keys"
        " ; chmod 600 ~/.ssh/authorized_keys ; "
        f"grep -c '{KEY_TAG}' ~/.ssh/authorized_keys"
    )
    code, out, err = run(client, cmd, quiet=True, check=False)
    if code == 0 and out.strip().isdigit():
        log(f"公钥已安装在 NAS（匹配 {out.strip()} 条），后续可免密登录。")


# ---------------- 远程执行 ----------------

def run(
    client: paramiko.SSHClient,
    cmd: str,
    sudo: bool = False,
    timeout: int = 600,
    check: bool = True,
    quiet: bool = False,
    stream: bool = False,
) -> tuple[int, str, str]:
    if sudo:
        # -S 从 stdin 读密码；-p '' 让提示为空，避免把提示混进输出
        full = f"sudo -S -p '' bash -lc {shlex.quote(cmd)}"
    else:
        full = f"bash -lc {shlex.quote(cmd)}"

    if not quiet:
        log(f"$ {cmd}")

    stdin, stdout, stderr = client.exec_command(full, timeout=timeout, get_pty=stream)
    if sudo and SUDO_PASSWORD:
        try:
            stdin.write(SUDO_PASSWORD + "\n")
            stdin.flush()
        except OSError:
            pass
    try:
        stdin.channel.shutdown_write()
    except Exception:  # noqa: BLE001
        pass

    if stream:
        # 长耗时步骤（镜像构建）实时输出，便于观察进度与尽早发现失败
        chunks: list[str] = []
        while True:
            data = stdout.channel.recv(4096)
            if not data:
                break
            text = data.decode("utf-8", "replace")
            chunks.append(text)
            sys.stdout.write(text)
            sys.stdout.flush()
        code = stdout.channel.recv_exit_status()
        out = "".join(chunks)
        err = stderr.read().decode("utf-8", "replace")
        if code != 0 and check:
            raise RuntimeError(f"远程命令失败（退出码 {code}）：{cmd}\n{err}")
        return code, out, err

    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()

    if out.strip() and not quiet:
        print(out.rstrip())
    if err.strip() and code != 0:
        print(f"[stderr] {err.rstrip()}", file=sys.stderr)
    elif err.strip() and not quiet and "password for" not in err.lower():
        print(f"[stderr] {err.rstrip()}", file=sys.stderr)

    if code != 0 and check:
        raise RuntimeError(f"远程命令失败（退出码 {code}）：{cmd}\n{err}")
    return code, out, err


# ---------------- 步骤 ----------------

def sftp_path(sftp: paramiko.SFTPClient, real_path: str) -> str:
    """把 NAS 上的真实路径换算成 SFTP 会话内可用路径。

    群晖会把 SSH 的 SFTP 子系统 chroot 到共享目录根：
    真实路径 /volume1/docker/fam-v21 在 SFTP 里应写成 /docker/fam-v21。
    这里先探测哪种写法可用，避免硬编码。
    """
    if not real_path.startswith("/volume1/"):
        return real_path
    candidate = real_path[len("/volume1"):]
    try:
        sftp.stat(candidate)
        return candidate
    except IOError:
        return real_path


def _probe_remote_ids(client: paramiko.SSHClient) -> None:
    global REMOTE_UID, REMOTE_GID
    code, out, _ = run(client, "id -u; id -g", quiet=True, check=False)
    lines = [l.strip() for l in out.strip().splitlines() if l.strip().isdigit()]
    if len(lines) >= 2:
        REMOTE_UID, REMOTE_GID = int(lines[0]), int(lines[1])


def step_probe(client: paramiko.SSHClient) -> None:
    log("探测 NAS 环境…")
    _, out, _ = run(
        client,
        "echo \"arch=$(uname -m)\"; echo \"os=$(uname -s)\"; echo \"user=$(id -un)\"; "
        "echo \"groups=$(id -Gn)\"; echo \"home=$HOME\"; echo \"shell=$SHELL\"",
        quiet=True,
    )
    for line in out.strip().splitlines():
        print("  " + line)

    _probe_remote_ids(client)

    code, out, _ = run(client, "sudo -n true", quiet=True, check=False)
    global SUDO_PASSWORD
    if code == 0:
        log("sudo：已免密，可直接执行")
        SUDO_PASSWORD = None
    elif SECRETS.get("SSH_PASS"):
        log("sudo：需要密码，已启用 sudo -S 自动喂入（不修改 NAS sudoers）")
        SUDO_PASSWORD = SECRETS["SSH_PASS"]
    else:
        warn("sudo 需要密码且未提供 SSH_PASS，docker 操作将无法执行。")

    code, out, err = run(client, "sudo -S -p '' docker version --format '{{.Server.Version}}'",
                         sudo=True, quiet=True, check=False)
    if code == 0:
        log(f"Docker 服务端版本：{out.strip()}")
    else:
        warn(f"docker 调用异常：{(err or out).strip()[:200]}")

    for label, cmd in (
        ("docker compose v2", "docker compose version"),
        ("docker-compose v1", "docker-compose version"),
    ):
        code, out, err = run(client, cmd, sudo=True, quiet=True, check=False)
        shown = (out or err).strip().splitlines()[0] if (out or err).strip() else "N/A"
        print(f"  {label}: {shown}")

    _, out, _ = run(client, "sudo -S -p '' docker ps --format '{{.Names}}|{{.Image}}|{{.Ports}}'",
                    sudo=True, quiet=True, check=False)
    print("  运行中的容器：")
    for line in out.strip().splitlines() or ["（无）"]:
        parts = line.split("|")
        print(f"    - {parts[0]}" + (f"  [{parts[1]}]" if len(parts) > 1 else ""))


def find_mysql(client: paramiko.SSHClient) -> dict | None:
    log("定位 NAS 上的 MySQL / MariaDB 容器…")
    _, out, _ = run(
        client,
        "sudo -S -p '' docker ps --format '{{.Names}}|{{.Image}}|{{.Ports}}' | grep -Ei 'mysql|mariadb' || true",
        sudo=True, quiet=True, check=False,
    )
    rows = [r.strip() for r in out.strip().splitlines() if r.strip()]
    if not rows:
        warn("未发现运行中的 MySQL/MariaDB 容器，改为依赖 .secrets.local 中的 MYSQL_* 凭据。")
        return None

    chosen: dict | None = None
    for row in rows:
        parts = (row.split("|") + ["", ""])[:3]
        name, image, ports = parts
        published = None
        for part in ports.split(","):
            if "->3306" in part:
                published = part.split("->")[0].split(":")[-1].strip()
        log(f"  候选容器：{name}（{image}）发布端口：{published or '未发布到宿主'}")
        if published and chosen is None:
            chosen = {"container": name, "port": published, "image": image}

    if chosen is None:
        warn("MySQL 容器未把 3306 发布到宿主，需要走容器内网方案。")
        return None

    # 读容器环境变量里的 root 口令（仅在本机内存中使用，不回显）
    _, env_out, _ = run(
        client,
        f"sudo -S -p '' docker inspect {shlex.quote(chosen['container'])} "
        "--format '{{range .Config.Env}}{{println .}}{{end}}'",
        sudo=True, quiet=True, check=False,
    )
    password = ""
    for line in env_out.splitlines():
        for key in ("MYSQL_ROOT_PASSWORD=", "MARIADB_ROOT_PASSWORD="):
            if line.startswith(key):
                password = line.split("=", 1)[1]
    chosen["root_password"] = password or SECRETS.get("MYSQL_PASS", "")
    log(f"  选定 MySQL 容器：{chosen['container']}，宿主端口 {chosen['port']}，"
        f"口令来源：{'容器环境变量' if password else '本机凭据文件'}（不回显）")
    return chosen


def ensure_database(client: paramiko.SSHClient, mysql: dict) -> None:
    log(f"创建/复用数据库 {DB_NAME}（utf8mb4；不动 gitea / zentao / accounting 等库）…")
    password = mysql.get("root_password") or SECRETS.get("MYSQL_PASS", "")
    user = SECRETS.get("MYSQL_USER", "root")
    if not password:
        warn("缺少 MySQL 口令，跳过自动建库；可手工执行 deploy/create_db.sql。")
        return

    sql = (
        f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
        "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
    )
    # 口令通过 stdin 传给容器内 mysql，避免出现在命令行参数（ps 可见）中
    cmd = (
        f"docker exec -i {shlex.quote(mysql['container'])} "
        f"mysql -u{shlex.quote(user)} -p -e {shlex.quote(sql)}"
    )
    stdin, stdout, stderr = client.exec_command(
        f"sudo -S -p '' bash -lc {shlex.quote(cmd)}", timeout=180
    )
    if SUDO_PASSWORD:
        stdin.write(SUDO_PASSWORD + "\n")
    stdin.write(password + "\n")
    stdin.flush()
    try:
        stdin.channel.shutdown_write()
    except Exception:  # noqa: BLE001
        pass
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    if code != 0:
        raise RuntimeError(f"建库失败：{err.strip()[:300]}")

    _, out, _ = run(
        client,
        f"sudo -S -p '' docker exec -i {shlex.quote(mysql['container'])} "
        f"mysql -u{shlex.quote(user)} -p{shlex.quote(password)} "
        f"-e \"SHOW DATABASES LIKE '{DB_NAME}';\"",
        sudo=True, quiet=True, check=False,
    )
    log(f"数据库 {DB_NAME} 就绪：{out.strip().splitlines()[-1] if out.strip() else '（已存在）'}")


def make_package() -> pathlib.Path:
    log("打包工程（排除依赖、数据、凭据与本机环境文件）…")
    out_path = LOCAL_ROOT / "deploy" / "fam-v21-src.tar.gz"
    targets = ["backend", "frontend", "docker-compose.yml", "deploy"]
    with tarfile.open(out_path, "w:gz") as tar:
        for target in targets:
            base = LOCAL_ROOT / target
            if not base.exists():
                continue
            if base.is_file():
                tar.add(base, arcname=target)
                continue
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
                rel = pathlib.Path(root).relative_to(LOCAL_ROOT)
                for name in files:
                    if name in EXCLUDE_FILES or pathlib.Path(name).suffix in EXCLUDE_SUFFIX:
                        continue
                    tar.add(pathlib.Path(root) / name, arcname=str(rel / name))
    size_mb = out_path.stat().st_size / 1024 / 1024
    log(f"工程包：{out_path.name}（{size_mb:.2f} MB）")
    return out_path


def upload(client: paramiko.SSHClient, package: pathlib.Path) -> None:
    log(f"准备远端目录 {REMOTE_DIR} …")
    run(client, f"mkdir -p {shlex.quote(REMOTE_DIR)}", sudo=True)
    if REMOTE_UID is not None and REMOTE_GID is not None:
        # 注意：chown 的目标 id 必须由客户端显式给出；写在 sudo bash -lc 里的 $(id -u) 会解析成 root
        run(client, f"chown -R {REMOTE_UID}:{REMOTE_GID} {shlex.quote(REMOTE_DIR)}", sudo=True)
    run(client, f"mkdir -p {shlex.quote(REMOTE_DIR)}/data/attachments "
                f"{shlex.quote(REMOTE_DIR)}/data/backups", sudo=True)
    if REMOTE_UID is not None and REMOTE_GID is not None:
        run(client, f"chown -R {REMOTE_UID}:{REMOTE_GID} {shlex.quote(REMOTE_DIR)}/data", sudo=True)

    sftp = client.open_sftp()
    remote_dir = sftp_path(sftp, REMOTE_DIR)
    remote_tar = f"{remote_dir}/fam-v21-src.tar.gz"
    log(f"上传工程包到 {remote_tar}（SFTP chroot 路径）…")
    sftp.put(str(package), remote_tar)
    sftp.close()

    # 解包前先清掉「完全由仓库决定」的前端源码目录：
    # tar 解包是覆盖式的，本地删掉/改名的文件（如换成矢量的 brands/*.png）会残留在远端，
    # 再被 docker build 的 COPY . . 打进镜像，出现新旧图标同时在 dist 里的情况。
    # public 与 src 没有任何运行期数据，删掉后由本次 tar 完整重建。
    run(client, (
        f"cd {shlex.quote(REMOTE_DIR)} && "
        "rm -rf frontend/public frontend/src && "
        "tar -xzf fam-v21-src.tar.gz && rm -f fam-v21-src.tar.gz"
    ), timeout=300)
    _, out, _ = run(client, f"ls -1 {shlex.quote(REMOTE_DIR)}", quiet=True)
    log(f"远端目录内容：{', '.join(out.split())}")


def _read_remote_env(client: paramiko.SSHClient) -> dict[str, str]:
    """读取 NAS 上已有的 fam-v21.env，用于复用密钥与专用账号口令。

    复用是必须的：
    - 重新生成 FAM_ENCRYPTION_KEY 会让此前加密存储的 OCR/第三方 AI/通知 Token 全部无法解密；
    - 重新生成数据库口令会与 MySQL 中已创建的账号不匹配。
    """
    try:
        sftp = client.open_sftp()
        path = f"{sftp_path(sftp, REMOTE_DIR)}/deploy/fam-v21.env"
        with sftp.open(path, "r") as fh:
            content = fh.read().decode("utf-8", "replace")
        sftp.close()
    except IOError:
        return {}

    data: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("FAM_JWT_SECRET=") and "__" not in line:
            data["jwt_secret"] = line.split("=", 1)[1].strip()
        elif line.startswith("FAM_ENCRYPTION_KEY=") and "__" not in line:
            data["encryption_key"] = line.split("=", 1)[1].strip()
        elif line.startswith("FAM_DATABASE_URL=") and "__" not in line:
            # 从既有 DSN 中还原专用账号与口令
            try:
                url = make_url(line.split("=", 1)[1].strip())
                if url.username and url.username != "root":
                    data["db_app_user"] = url.username
                if url.password:
                    data["db_app_password"] = url.password
            except Exception:  # noqa: BLE001
                pass
    if data:
        log("检测到既有环境变量文件：沿用原有 JWT 密钥、加密主密钥与数据库账号口令")
    return data


def ensure_app_user(client: paramiko.SSHClient, mysql: dict, app_user: str, app_password: str) -> None:
    """创建仅授权 fam 库的专用账号；不使用 root 连接应用数据库。"""
    root_password = mysql.get("root_password") or SECRETS.get("MYSQL_PASS", "")
    if not root_password:
        raise RuntimeError("缺少 MySQL root 口令，无法创建专用账号")
    container = mysql["container"]

    statements = []
    for host in DB_APP_HOSTS:
        account = f"'{app_user}'@'{host}'"
        statements.append(f"CREATE USER IF NOT EXISTS {account} IDENTIFIED BY '{app_password}';")
        statements.append(f"ALTER USER {account} IDENTIFIED BY '{app_password}';")
        statements.append(f"GRANT ALL PRIVILEGES ON `{DB_NAME}`.* TO {account};")
        # 明确收回全局权限，确保最小权限
        statements.append(f"REVOKE ALL PRIVILEGES ON *.* FROM {account};")
        statements.append(f"GRANT ALL PRIVILEGES ON `{DB_NAME}`.* TO {account};")
    statements.append("FLUSH PRIVILEGES;")
    sql = " ".join(statements)

    cmd = f"docker exec -i {shlex.quote(container)} mysql -uroot -p -e {shlex.quote(sql)}"
    stdin, stdout, stderr = client.exec_command(f"sudo -S -p '' bash -lc {shlex.quote(cmd)}", timeout=180)
    if SUDO_PASSWORD:
        stdin.write(SUDO_PASSWORD + "\n")
    stdin.write(root_password + "\n")
    stdin.flush()
    try:
        stdin.channel.shutdown_write()
    except Exception:  # noqa: BLE001
        pass
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    if code != 0:
        raise RuntimeError(f"创建专用账号失败：{err.strip()[:300]}")

    # 复核授权范围
    verify = (
        f"SELECT user,host FROM mysql.user WHERE user='{app_user}'; "
        f"SHOW GRANTS FOR '{app_user}'@'{DB_APP_HOSTS[0]}';"
    )
    cmd = f"docker exec -i {shlex.quote(container)} mysql -uroot -p -e {shlex.quote(verify)}"
    stdin, stdout, stderr = client.exec_command(f"sudo -S -p '' bash -lc {shlex.quote(cmd)}", timeout=120)
    if SUDO_PASSWORD:
        stdin.write(SUDO_PASSWORD + "\n")
    stdin.write(root_password + "\n")
    stdin.flush()
    try:
        stdin.channel.shutdown_write()
    except Exception:  # noqa: BLE001
        pass
    out = stdout.read().decode("utf-8", "replace")
    stdout.channel.recv_exit_status()
    log("已创建/更新专用数据库账号（仅授权 fam 库）：")
    for line in out.strip().splitlines():
        if line.strip():
            print("  " + line)


def write_env(client: paramiko.SSHClient, mysql: dict | None,
              app_user: str = DB_APP_USER, app_password: str = "") -> None:
    log("写入生产环境变量文件（权限 600，含密钥，不回显）…")
    existing = _read_remote_env(client)
    jwt_secret = existing.get("jwt_secret") or secrets.token_urlsafe(48)
    fernet_key = existing.get("encryption_key") or base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()

    # 应用侧使用专用账号，绝不使用 root（最小权限原则）
    db_user = app_user
    raw_password = app_password or existing.get("db_app_password") or ""
    if not raw_password:
        raise RuntimeError("缺少数据库应用账号口令，无法生成数据库连接串")

    # 口令必须 URL 编码：含 @ : / ? # % 时直接拼接会让 DSN 解析错位
    db_password = urllib.parse.quote_plus(raw_password)
    db_host = nas_host()
    db_port = str((mysql or {}).get("port") or 3306)

    template = (LOCAL_ROOT / "deploy" / "fam-v21.env.example").read_text(encoding="utf-8")
    content = template
    replacements = {
        "__MYSQL_USER__": urllib.parse.quote_plus(db_user),
        "__MYSQL_PASSWORD__": db_password,
        "__MYSQL_DB__": DB_NAME,
        "__MYSQL_PORT__": db_port,
        "__NAS_HOST__": db_host,
        "__JWT_SECRET__": jwt_secret,
        "__ENCRYPTION_KEY__": fernet_key,
    }
    for key, value in replacements.items():
        content = content.replace(key, value)

    sftp = client.open_sftp()
    remote_env = f"{sftp_path(sftp, REMOTE_DIR)}/deploy/fam-v21.env"
    with sftp.open(remote_env, "w") as fh:
        fh.write(content)
    sftp.close()
    run(client, f"chmod 600 {shlex.quote(REMOTE_DIR)}/deploy/fam-v21.env", sudo=True)
    if REMOTE_UID is not None and REMOTE_GID is not None:
        run(client, f"chown {REMOTE_UID}:{REMOTE_GID} {shlex.quote(REMOTE_DIR)}/deploy/fam-v21.env", sudo=True)
    log("环境变量文件已写入 NAS（600），口令与密钥仅存 NAS。")


def compose_cmd(client: paramiko.SSHClient) -> str:
    code, _, _ = run(client, "docker compose version", sudo=True, quiet=True, check=False)
    return "docker compose" if code == 0 else "docker-compose"


def step_build(client: paramiko.SSHClient) -> None:
    dc = compose_cmd(client)
    log(f"构建镜像（{dc} build，首次构建耗时较长，实时输出如下）…")
    run(client, f"cd {shlex.quote(REMOTE_DIR)} && {dc} build --progress plain",
        sudo=True, timeout=5400, stream=True)


def step_up(client: paramiko.SSHClient) -> None:
    dc = compose_cmd(client)
    log("启动容器…")
    run(client, f"cd {shlex.quote(REMOTE_DIR)} && {dc} up -d", sudo=True, timeout=900)
    time.sleep(8)
    run(client, f"cd {shlex.quote(REMOTE_DIR)} && {dc} ps", sudo=True, check=False)


def step_verify(client: paramiko.SSHClient) -> None:
    log("校验后端启动与数据库迁移…")
    run(client, "docker logs --tail 40 fam-v21-api 2>&1 || true", sudo=True, check=False)
    run(client, "docker exec fam-v21-api curl -fsS http://127.0.0.1:8000/api/v1/health || true",
        sudo=True, check=False, timeout=120)
    log("校验前端与接口连通性…")
    run(client, "curl -s -o /dev/null -w 'frontend HTTP %{http_code}\\n' http://127.0.0.1:18080/ || true",
        check=False)
    run(client, "curl -s http://127.0.0.1:18080/api/v1/setup/status || true", check=False)
    log(f"访问地址：http://{nas_host()}:18080/  （API 健康检查：http://{nas_host()}:18000/api/v1/health）")


def step_e2e() -> None:
    """上线前置门禁：本地全链路 E2E（真实后端 + 前端 + Chromium）。

    必须在触碰 NAS 之前执行：E2E 未通过说明页面层有回归，直接中止部署更安全。
    """
    script = PROJECT_ROOT / "deploy" / "e2e" / "run.mjs"
    if not script.exists():
        raise SystemExit(f"E2E 脚本不存在：{script}")
    node = shutil.which("node")
    if not node:
        raise SystemExit("未找到 node，无法执行 E2E 门禁（可用 --skip-e2e 跳过，但不建议）")

    log("运行全链路 E2E 门禁（本地 8000 / 5173 + Chromium）…")
    proc = subprocess.run([node, str(script)], cwd=str(PROJECT_ROOT))
    if proc.returncode != 0:
        raise SystemExit("E2E 门禁未通过，已中止部署；详见 E2E-全链路测试报告.md 与 deploy/e2e/screenshots/")
    log("E2E 门禁通过")


def main() -> int:
    global SECRETS
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", default="all",
                        choices=["probe", "mysql", "upload", "build", "up", "verify", "e2e", "all"])
    parser.add_argument("--no-sync", action="store_true",
                        help="跳过上传与写环境变量（远端代码未变时复用已有产物，便于快速重启/校验）")
    parser.add_argument("--skip-e2e", action="store_true",
                        help="跳过上线前的本地全链路 E2E 门禁（不建议）")
    args = parser.parse_args()

    # 门禁先跑：纯本地，失败时不必连远端
    if args.step == "e2e":
        step_e2e()
        return 0
    if args.step == "all" and not args.skip_e2e:
        step_e2e()

    SECRETS = load_secrets()

    client, method = connect()
    log(f"已连接 {SECRETS.get('SSH_USER', DEFAULT_SSH_USER)}@{nas_host()}（认证方式：{method}）")
    try:
        if method == "密码":
            install_public_key(client)

        if args.step == "probe":
            step_probe(client)
            return 0

        step_probe(client)
        mysql = find_mysql(client)
        if args.step == "mysql":
            if mysql:
                ensure_database(client, mysql)
                existing = _read_remote_env(client)
                app_user = existing.get("db_app_user") or DB_APP_USER
                app_password = existing.get("db_app_password") or secrets.token_hex(20)
                ensure_app_user(client, mysql, app_user, app_password)
            return 0

        if args.no_sync:
            log("已指定 --no-sync：跳过建库、上传与环境变量写入，直接使用 NAS 上既有产物。")
        else:
            existing = _read_remote_env(client)
            app_user = existing.get("db_app_user") or DB_APP_USER
            app_password = existing.get("db_app_password") or secrets.token_hex(20)
            if mysql:
                ensure_database(client, mysql)
                ensure_app_user(client, mysql, app_user, app_password)
            package = make_package()
            upload(client, package)
            write_env(client, mysql, app_user, app_password)

        if args.step == "upload":
            return 0
        if args.step in ("all", "build"):
            step_build(client)
            if args.step == "build":
                return 0
        if args.step in ("all", "up"):
            step_up(client)
            if args.step == "up":
                return 0
        step_verify(client)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
