-- ask_apps: 应用主档(应用管理的唯一主表,后续档案同步/凭据/调度都挂它)。
-- 本文件是唯一 DDL 来源: 新库由 store_migrations._SCHEMA 执行;老库差异
-- 走迁移(列名规约 + region 补列 + 列序重建)。
-- 设计要点:
--   * app_code = entry_points 注册表域名,是**全库关联键**——
--     ask_accounts / ask_signin_runs 的 domain 列值即本表 app_code,
--     join 关系: <表>.domain = ask_apps.app_code
--   * region 区分国内版/国际版(qoder cn / qoder intl 这类变体): 同一
--     app_code 未来出现第二实现时,注册表域名加后缀(如 qoder_intl),
--     本表各占一行;当前每应用一行,region 默认 'cn'
--   * 域的存在性跟代码走(注册表 + 域 YAML),本表只承载管理态与档案,
--     行由调用方惰性补齐
-- 列序习惯: 主键 → 业务主体 → 开关 → 展示与描述 → 统一审计字段。
CREATE TABLE IF NOT EXISTS ask_apps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    app_code    TEXT    NOT NULL,  -- 应用编码: 注册表域名,全库关联键
    region      TEXT    NOT NULL DEFAULT 'cn',  -- 版本区: cn / intl(国内版/国际版)
    enabled     INTEGER NOT NULL DEFAULT 1,  -- 应用总开关(压在账户级 enabled 之上)
    app_name    TEXT,              -- 应用显示名(NULL = 用注册表 DISPLAY_NAME)
    app_desc    TEXT,              -- 应用描述/备注(NULL = 用注册表 DESCRIPTION/声明 desc)
    source      TEXT    NOT NULL DEFAULT 'auto',  -- 行来源: auto(同步扫描) / manual(用户新建)
    -- 统一审计字段(所有表必备): 行惰性补齐创建,更新时由写层维护
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
    UNIQUE(app_code, region)
);
