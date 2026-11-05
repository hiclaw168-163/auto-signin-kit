-- ask_signin_runs: 签到运行台账(一行一次运行,断签/积分问题的数据源)。
-- 本文件是唯一 DDL 来源: 新库由 store_migrations._SCHEMA 执行,老库差异走
-- _MIGRATIONS 补列,两处不许各自漂移。全库时间统一本地无时区
-- 'YYYY-MM-DD HH:MM:SS'(时区不逐行带,统一机器系统时区)。
CREATE TABLE IF NOT EXISTS ask_signin_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    executed_at     TEXT    NOT NULL,  -- 执行时间(本地无时区)
    domain          TEXT    NOT NULL,  -- workbuddy / qoder / trae / doubao
    action          TEXT,              -- 干了什么: auto / active / claim / backfill ...
    source          TEXT    NOT NULL DEFAULT 'run',  -- 数据哪来的: run / backfill
    result          TEXT    NOT NULL,  -- 标准词表 10 词(见 core/report.py RESULT_LABELS)
    needs_attention INTEGER NOT NULL DEFAULT 0,
    report          TEXT,              -- 一句人话摘要
    request         TEXT,              -- 请求参数 JSON(动作入参;与 response 一一对应)
    response        TEXT,              -- 响应结果 JSON(http 码/body/积分等业务返回)
    detail          TEXT    NOT NULL,  -- 完整事件 JSON(兼容保留,新读方用 request/response)
    account         TEXT    DEFAULT 'default',  -- 本次运行用的账户(积分业务主体维度)
    -- 统一审计字段(所有表必备): 行创建/最近更新时间,由 DDL 默认值兜底,
    -- ingest 显式写与 executed_at 同值
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
);
