# NAS 一次性授权命令（FAM V2.1 部署）

> 目的：让本机可以免密 SSH 到 NAS 并执行 Docker 部署。
> 授权只在**本次部署期间**需要，部署完成后可按最后一节撤销。

---

## 第 0 步：确认前提

在 NAS 上需要有可用的用户主目录（`~/.ssh` 才能生效）：

- DSM →「控制面板」→「用户与群组」→「高级」→ 勾选 **启用用户主目录**（若已是启用状态则跳过）
- 「控制面板」→「终端机和 SNMP」→ 勾选 **启用 SSH 功能**（端口 22，已确认可达）

---

## 第 1 步：本机 → NAS 登录一次

在**你自己的电脑**上打开终端（Windows 用 PowerShell 或 Git Bash），执行：

```bash
ssh nasadmin@<NAS_IP>
```

输入你的 NAS 登录密码进入。

---

## 第 2 步：在 NAS 上粘贴执行（安装公钥）

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
grep -q "fam-v21-deploy@workbuddy" ~/.ssh/authorized_keys 2>/dev/null || \
  echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKYiqg9hIIjjfzh+sS8hxAgpT9KD3BOvag//3FR/izN0 fam-v21-deploy@workbuddy' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
echo "公钥安装完成"; ls -l ~/.ssh/authorized_keys
```

---

## 第 3 步：在 NAS 上粘贴执行（部署窗口免密 sudo）

我需要在 NAS 上执行 `docker build` / `docker compose` / 建库与目录操作，这些都需要 root 权限。
群晖的 `sudo` 默认要求输入密码，而自动化脚本无法交互输入，因此需要临时放开免密：

```bash
echo '%administrators ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/fam-v21-deploy >/dev/null
sudo chmod 440 /etc/sudoers.d/fam-v21-deploy
sudo -n true && echo "免密 sudo 已生效"
```

> ⚠️ 说明：这条规则等同于给 administrators 组 root 级权限，属于**临时授权**。
> 如果你不接受，可以改走"你自己执行部署脚本"的方式（我提供脚本与全部校验步骤），告诉我即可。

---

## 第 4 步：告诉我一声

回我一句「已授权」即可，我会自动完成：
探测环境 → 定位 MySQL 容器并读取口令 → 建 `fam` 库 → 上传工程 → 构建镜像 → 起容器 → 迁移 → 健康校验。

---

## 部署完成后撤销授权（建议执行）

```bash
sudo rm -f /etc/sudoers.d/fam-v21-deploy
sudo -n true 2>/dev/null && echo "仍可免密（未生效）" || echo "已撤销免密 sudo"

# 如需同时收回 SSH 免密登录
sed -i '/fam-v21-deploy@workbuddy/d' ~/.ssh/authorized_keys
```

---

## 附：只读核对信息

| 项 | 值 |
|---|---|
| NAS 地址 | <NAS_IP> |
| 已确认开放端口 | 22（SSH）、3306（MySQL）、80、443 |
| 部署目录 | `/volume1/docker/fam-v21` |
| 对外端口 | 前端 18080 → 容器 80；后端 18000 → 容器 8000 |
| 数据库 | 沿用你选择的库名 `fam`（utf8mb4），不动 gitea / zentao / accounting 等其他库 |
| MySQL 口令 | 由脚本从 `docker inspect` 读取容器环境变量，**无需你提供**，且不会回显或写入本机 |
