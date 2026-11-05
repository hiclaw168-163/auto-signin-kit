-- ask_apps: 应用管理态(应用总开关/展示名覆盖/备注)。
-- 本文件是唯一 DDL 来源: 新库由 store_migrations._SCHEMA 执行;新表无需
-- 老库迁移(CREATE TABLE IF NOT EXISTS,行由调用方惰性补齐)。
-- 设计边界: 域的存在性跟代码走(entry_points 注册表 + 域 YAML),这里只放
-- 运行期管理态——停用某应用不用改它的 YAML 或注销调度,run_action 静默
-- 触发时直接跳过(手动执行永远放行,与账户级 enabled 同语义)。
-- 列序习惯: 主键 → 业务主体 → 开关 → 展示与备注 → 统计审计字段。
CREATE TABLE IF NOT EXISTS ask_apps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    domain      TEXT    NOT NULL,  -- 业务键: entry_points 注册表域名
    enabled     INTEGER NOT NULL DEFAULT 1,  -- 应用总开关(压在账户级 enabled 之上)
    display     TEXT,              -- 展示名覆盖(NULL = 用注册表 DISPLAY_NAME)
    note        TEXT,              -- 备注(UI 可编辑)
    -- 统一审计字段(所有表必备): 行由惰性补齐创建,开关操作更新 updated_at
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
    UNIQUE(domain)
);
