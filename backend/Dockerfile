# FAM V2.1 后端镜像（FastAPI + SQLAlchemy Async + Alembic）
FROM python:3.13-slim

# 国内网络下官方源会长时间卡住（实测 pypi.org 从 NAS 访问 10s 超时），
# 故默认走国内镜像；可通过 --build-arg 覆盖为官方源。
ARG APT_MIRROR=mirrors.ustc.edu.cn
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    TZ=Asia/Shanghai

WORKDIR /app

# 只装运行期必需：curl（健康检查）、tzdata（时区）。
# 不装 build-essential：FastAPI/SQLAlchemy/cryptography/bcrypt/greenlet 等在本平台均有预编译 wheel，
# 装编译器只会显著拉长构建时间并放大镜像体积。
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl tzdata; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 运行期目录（用卷挂载持久化）
RUN mkdir -p /app/data/attachments /app/data/backups

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

# 迁移失败则容器退出，不启动半升级系统（22 §5.2 / 24 §9）
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
