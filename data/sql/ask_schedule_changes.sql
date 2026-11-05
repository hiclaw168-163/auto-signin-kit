-- ask_schedule_changes: 调度变更审计(定时任务注册/注销动作记录)。
-- 本文件是唯一 DDL 来源: 新库由 store_migrations._SCHEMA 执行,老库差异走迁移。
CREATE TABLE IF NOT EXISTS ask_schedule_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    executed_at TEXT    NOT NULL,  -- 变更时间(本地无时区)
    action      TEXT    NOT NULL,  -- install / uninstall
    platform    TEXT,
    tasks       TEXT,              -- 本次变更涉及的任务与时间(JSON 数组)
    report      TEXT,
    -- 统一审计字段(所有表必备): 审计行只插不改,两值同 executed_at
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
);
