-- FAM V2.1 建库脚本（手动兜底）
-- 用途：当自动部署脚本无法读取 MySQL 容器口令时，由你在 NAS 上手工执行。
--
-- 执行方式（在 NAS 的 SSH 里）：
--   sudo docker exec -i <MySQL容器名> mysql -uroot -p < create_db.sql
--
-- 安全说明：
--   1. 只创建/复用 fam 库，不触碰 gitea / zentao / accounting / invoice_manager 等其他库；
--   2. 不创建、不修改任何业务表 —— 表结构统一由后端容器的
--      `alembic upgrade head` 迁移创建，避免出现第二套表结构；
--   3. 不使用 DROP，不删除任何既有对象。

CREATE DATABASE IF NOT EXISTS `fam`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- 核对结果
SHOW DATABASES LIKE 'fam';
SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME
FROM information_schema.SCHEMATA
WHERE SCHEMA_NAME = 'fam';
