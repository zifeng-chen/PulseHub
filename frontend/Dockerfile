# FAM V2.1 前端镜像（Vue 3 + Vite 构建 → nginx 静态托管 + /api 反向代理）
# 基础镜像选用 alpine 版本：NAS 上已有 node:22-alpine / nginx:1.27-alpine，可免去境外拉取。
FROM node:22-alpine AS build

# 国内网络下 npm 官方源不稳定，默认走 npmmirror（实测 0.09s）；可 --build-arg 覆盖。
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm config set registry ${NPM_REGISTRY}

WORKDIR /web

# 先装依赖，利用层缓存
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY . .
# 直接跑 vite build：类型检查（vue-tsc）在本地/CI 完成，避免在 NAS 上构建时占用过多内存
RUN npx vite build

# ---- 运行阶段 ----
FROM nginx:1.27-alpine

ENV TZ=Asia/Shanghai

COPY --from=build /web/dist /usr/share/nginx/html
# 构建上下文来自 Windows，public/ 下的文件可能带 770 权限；
# nginx worker 不是 root，权限不足会直接 403（图标、manifest 打不开就是这个原因）
RUN chmod -R a+rX /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# 用 alpine 自带 busybox wget 做健康检查，避免为装 curl 额外依赖 apk 网络源
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
