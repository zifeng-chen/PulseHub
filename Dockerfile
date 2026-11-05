# PulseHub 单容器镜像
# 一个容器里同时跑：nginx（静态站点 + /api 反代）+ uvicorn（FastAPI），由 supervisord 托管。
# 对外只暴露 80（宿主映射为 6065），前端与接口同源，不再需要第二个容器 / 第二个端口。

# ---------- 阶段 1：构建前端 ----------
FROM node:22-alpine AS webbuild

# 国内网络下 npm 官方源不稳定，默认走 npmmirror；可 --build-arg 覆盖。
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm config set registry ${NPM_REGISTRY}

WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
# 类型检查（vue-tsc）在本地/CI 完成，NAS 上只跑 vite build，避免占内存
RUN npx vite build

# ---------- 阶段 2：运行 ----------
FROM python:3.13-slim

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

# nginx（静态 + 反代）、supervisor（进程托管）、curl（健康检查）、tzdata（时区）
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends nginx supervisor curl tzdata; \
    rm -rf /var/lib/apt/lists/*; \
    rm -f /etc/nginx/sites-enabled/default

# 后端依赖与代码
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
RUN mkdir -p /app/data/attachments /app/data/backups

# 前端构建产物
COPY --from=webbuild /web/dist /usr/share/nginx/html
RUN chmod -R a+rX /usr/share/nginx/html

# 站点配置：/api 直接反代到同容器内的 uvicorn（127.0.0.1:8000）
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

# nginx 日志接容器标准输出，docker logs 能直接看到
RUN ln -sf /dev/stdout /var/log/nginx/access.log \
 && ln -sf /dev/stderr /var/log/nginx/error.log

# 进程托管与启动脚本
COPY deploy/supervisord.conf /etc/supervisor/conf.d/pulsehub.conf
COPY deploy/start-nginx.sh /usr/local/bin/start-nginx.sh

COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh /usr/local/bin/start-nginx.sh

EXPOSE 80

# 入口脚本先校验环境变量 → 跑 alembic 迁移 → 再 exec supervisord 起 nginx + uvicorn。
# 迁移失败容器直接退出，不允许以半升级状态对外服务。
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/pulsehub.conf", "-n"]
