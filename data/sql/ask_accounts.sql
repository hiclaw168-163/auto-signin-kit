-- ask_accounts: 账户维表(哪个域的哪个账户,积分业务的主体维度;支持多户)。
-- 本文件是唯一 DDL 来源,store_migrations 的建表与列序重建(_reorder)共用。
CREATE TABLE IF NOT EXISTS ask_accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    domain          TEXT    NOT NULL,  -- 所属域(workbuddy/qoder/trae/doubao)
    name            TEXT    NOT NULL,  -- 账户标识(脱敏后入库;用户名/邮箱/尾号)
    device_id       TEXT,              -- 设备标识(多账户轮询的关键维度)
    client_version  TEXT,              -- 客户端版本(注册表/客户端数据文件)
    uid             TEXT,              -- 账户侧唯一标识(如 workbuddy uid)
    daily_potential INTEGER,           -- 每日可获取积分上限(NULL = 未设定)
    enabled         INTEGER NOT NULL DEFAULT 1,
    install_path    TEXT,              -- 安装路径/数据目录(两种域方言统一)
    client_info     TEXT,              -- 档案溢出 JSON(机器信息/域特有字段)
    info_synced_at  TEXT,              -- 档案同步时间(本地无时区)
    -- 统一审计字段(所有表必备);账户行会被更新,updated_at 由写层维护
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
    UNIQUE(domain, name)
);
