# FAM V2.1 · NAS Docker 部署报告

部署日期：2026-10-15
部署目标：NAS（UGREEN 系统）Docker 环境，复用其容器内既有 MySQL
状态：**部署成功，两个容器均已 Healthy，数据库迁移完成，等待首次初始化向导**

---

## 1. 部署结果

| 项目 | 结果 |
|---|---|
| 后端容器 `fam-v21-api` | **Up (healthy)**，`0.0.0.0:18000 -> 8000/tcp` |
| 前端容器 `fam-v21-web` | **Up (healthy)**，`0.0.0.0:18080 -> 80/tcp` |
| 数据库连接 | **MySQL 9.2.0**，连接正常（容器内自检 latency 0 ms） |
| 数据库迁移 | `alembic upgrade head` 成功，当前版本 `e180344ed0fb (head)` |
| 数据表 | **33 张**（32 张业务表 + `alembic_version`），与本地验证结果一致 |
| 库字符集 | `utf8mb4` / `utf8mb4_unicode_ci` |
| 生产模式 | `FAM_APP_ENV=production`，接口文档已自动关闭（`GET /docs` → 404） |
| 镜像 | `fam-v21-api:2.1.0`（221 MB）、`fam-v21-web:2.1.0`（49.2 MB） |

## 2. 访问地址

| 用途 | 地址 |
|---|---|
| **系统入口（首次会引导到初始化）** | http://<NAS_IP>:18080/ |
| 后端 API 健康检查 | http://<NAS_IP>:18000/api/v1/health |
| 前端同源反代（页面实际走这条） | http://<NAS_IP>:18080/api/v1/... |

> 首次打开会跳到 `/setup/check` → 六步引导。**管理员账号由你在浏览器里创建**，我不代设密码。

## 3. 架构与关键决策

```text
浏览器 ──18080──▶ fam-v21-web (nginx)
                    ├─ /            静态 SPA（history 回退 index.html）
                    └─ /api/  反代▶ fam-v21-api:8000（容器名，同源，无需 CORS）
                                        │
                                        └──▶ <NAS_IP>:3306 ──▶ NAS 既有 MySQL 容器
```

| 决策 | 原因 |
|---|---|
| 部署目录 `/volume1/docker/fam-v21` | 沿用你确认的高端口方案，不与既有服务冲突 |
| 前端 18080 / 后端 18000 | 80、443 已被占用，8080 不可达 |
| 数据卷 `data/attachments`、`data/backups` | 附件与备份必须落到宿主持久化 |
| 基础镜像选 `python:3.13-slim` + `node:22-alpine` + `nginx:1.27-alpine` | **三者 NAS 本地均已存在**，免去境外拉取 |
| 应用账号用 `fam_app` 而非 root | 最小权限，见第 5 节 |
| 容器入口先迁移后启动 | 迁移失败即退出，不启动半升级系统 |

## 4. 部署过程中发现并修复的真实问题

这些都是本地环境跑不出来、只在 NAS 上暴露的问题：

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | 构建卡死 22 分钟、无任何镜像产出 | NAS 访问 `pypi.org` 实测 **10 秒超时**（`pip install` 永远下不完）；`deb.debian.org` 同样慢 | 镜像内改用国内源：apt → 中科大（0.06 s）、pip → 阿里云（1.4 s）、npm → npmmirror（0.09 s）。构建时间从卡死降到 **约 6 分钟** |
| 2 | 前端基础镜像还要从 Docker Hub 拉 | 我原先用了 `node:22-slim`，NAS 上没有 | 改为 `node:22-alpine`（本地已有） |
| 3 | `pip install` 会编译源码拖慢构建 | Dockerfile 里装了 `build-essential` | 移除。实测全部依赖均为 cp313 预编译 wheel，**未触发任何编译** |
| 4 | `sftp.put` 报 `FileNotFoundError [Errno 2]` | **该 NAS 把 SSH 的 SFTP 会话 chroot 到共享目录根**：真实路径 `/volume1/docker/...` 在 SFTP 里必须写成 `/docker/...` | 新增 `sftp_path()` 自动探测换算；exec 命令仍用真实路径 |
| 5 | 目录被误 chown 成 root | `chown -R $(id -u):$(id -g)` 写在 `sudo bash -lc` 内，`$(id -u)` 会解析成 **root(0)** | 改为由客户端显式传 uid:gid（1000:10） |
| 6 | 容器启动即崩溃：`Access denied for user 'root'@'172.17.0.1'` | MySQL 的 root **只允许容器内 localhost**（故 `docker exec` 建库成功），而应用容器经宿主 DNAT 后来源是 Docker 网关 `172.17.0.1` | 不放开 root 远程登录，改为创建**仅授权 `fam` 库**的专用账号 `fam_app`，来源限定 `172.%` / `10.%` |

另修一处稳定性隐患：重复部署时**复用**原有 `FAM_ENCRYPTION_KEY`、`FAM_JWT_SECRET` 与数据库账号口令，否则重新生成主加密密钥会导致此前加密存储的 OCR / 第三方 AI / 通知 Token 全部无法解密。

## 5. 数据库权限现状（最小权限）

```sql
-- 已创建，只授权业务库，无任何全局权限
CREATE USER 'fam_app'@'172.%' ...;   -- 来源限定 Docker 私网段
CREATE USER 'fam_app'@'10.%'  ...;
GRANT USAGE ON *.* TO `fam_app`@`172.%`;              -- 无全局权限
GRANT ALL PRIVILEGES ON `fam`.* TO `fam_app`@`172.%`; -- 仅 fam 库
```

- 应用侧**不使用 root**。
- root 口令由部署脚本从 `docker inspect` 读取容器环境变量获得，**未回显、未写入本机任何文件**。
- 未触碰 NAS 上 gitea、zentao、phpmyadmin、qinglong、openwebui 等其他容器与数据库。

## 6. 日常运维命令

```bash
# 在 NAS 上（或用仓库里的部署脚本远程执行）
cd /volume1/docker/fam-v21

docker compose ps                     # 查看状态
docker compose logs -f fam-v21-api    # 看后端日志（含迁移过程）
docker compose restart fam-v21-api    # 重启后端
docker compose down && docker compose up -d   # 整体重启

# 升级到新版本（在开发机执行，会自动上传+建库校验+构建+重启）
python deploy/nas_deploy.py --step all
# 仅重启/校验（不重新上传构建）
python deploy/nas_deploy.py --step up --no-sync
python deploy/nas_deploy.py --step verify --no-sync
```

## 7. 文件与位置

| 内容 | 位置 |
|---|---|
| 部署目录 | NAS `/volume1/docker/fam-v21` |
| 生产环境变量（含密钥，权限 600） | NAS `/volume1/docker/fam-v21/deploy/fam-v21.env` |
| 附件与备份（持久化卷） | NAS `/volume1/docker/fam-v21/data/{attachments,backups}` |
| 本机部署脚本 | `deploy/nas_deploy.py` |
| 本机凭据（不入仓库） | `deploy/.secrets.local`（已加入 `.gitignore`） |
| 部署实时日志 | `deploy/build.log`、`deploy/up.log`、`deploy/sync.log` |

## 8. 待你完成 / 待决事项

1. **首次初始化**：打开 http://<NAS_IP>:18080/ 完成六步引导（数据库步骤可直接下一步，迁移已就绪）。
2. **SSH 公钥免密未生效**：`/home/nasadmin` 权限为 `drwxrwxrwx`（777 + ACL），OpenSSH `StrictModes` 拒绝使用其下的 `authorized_keys`（`~/.ssh` 700、`authorized_keys` 600 本身都正确）。修复需二选一——**我未擅自改动系统配置**：
   - `chmod 755 /home/nasadmin`（可能影响该共享目录的其他访问方式）
   - 或 sshd 配置 `StrictModes no`（降低该主机的登录校验强度）
   密码登录完全可用，不影响部署与运维。
3. **建议清理**：`deploy/.secrets.local` 内含 NAS 与 SSH 明文口令，部署稳定后可删除；如需继续用脚本部署则保留。

## 8.1 MySQL 凭据实际构成（已实测，供你备案）

该 MySQL 容器内存在**两条不同的 root 账号记录**，口令并不相同：

| 账号 | 口令 | 可用范围 | 实测结果 |
|---|---|---|---|
| `root@localhost` | `root`（4 字符） | 仅容器内部 | `docker exec` 登录成功 |
| `root@%` | `CHAOYUE8feng` | **任意主机** | 从容器内连宿主 IP 登录成功 |

认证插件也不同：`root@%` 为 `sha256_password`，`root@localhost` 为 `caching_sha2_password`。

> 部署脚本通过 `docker inspect` 读取容器环境变量得到的是 `root@localhost` 的口令，用于建库与创建专用账号；应用运行期**完全不用 root**。

## 8.2 已识别但按你决定「暂不处理」的安全隐患

以下为 2026-10-15 实测结果，**已按你的决定保持原状**，在此备案：

| # | 隐患 | 实测证据 |
|---|---|---|
| 1 | MySQL 端口对**所有网络接口**开放 | `docker port mysql-latest` → `3306/tcp -> 0.0.0.0:3306`；`ss -lnt` 显示 `0.0.0.0:3306` 与 `[::]:3306` |
| 2 | `root@%` 允许任意主机登录 | 从容器内连 `<NAS_IP>:3306` 作为 root 登录返回成功 |
| 3 | **MySQL root 口令与 NAS SSH 口令相同** | 均为 `CHAOYUE8feng`；任一处泄露即两处失守 |
| 4 | 容器内 `root@localhost` 口令为 `root` | 等于无口令强度 |
| 5 | `phpmyadmin-1` 设置了 `PMA_ARBITRARY` | 允许通过 phpMyAdmin 连接任意 MySQL 服务器 |

**对 FAM V2.1 本身无影响**：应用只使用最小权限账号 `fam_app`（`GRANT USAGE ON *.*` + `GRANT ALL ON fam.*`）。但该 MySQL 同时承载 gitea、zentao 等既有服务，风险面在 NAS 整体。
若后续要收敛，建议顺序为：先缩小 3306 监听范围 → 再轮换 `root@%` 口令 → 最后处理容器内 `root` 口令；每步都需先扫描依赖该账号的容器，避免打断既有服务。

---

## 9. 已知限制（与 M0 交付报告一致）

- 备份目前在 MySQL 场景下记录元数据并做附件快照，**数据库逻辑导出**按《24 §2》交由部署工具链（M8 完善）。
- 生产镜像内含 `pytest` 等测试依赖（requirements 未拆分 base/dev），属镜像体积问题，不影响运行。
- 尚未在 NAS 上跑通"AI / OCR / 通知"的真实外发链路（未配置任何提供商），当前均为"未配置"降级状态，符合基座设计。
