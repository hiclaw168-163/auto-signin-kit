-- ask_apps: 应用主档(应用管理的唯一主表,后续档案同步/凭据/调度都挂它)。
-- 本文件是唯一 DDL 来源: 新库由 store_migrations._SCHEMA 执行;老库差异
-- 走迁移(列名规约 + 补列 + 列序重建)。
-- 设计要点:
--   * app_code = entry_points 注册表域名,是**全库关联键**——
--     ask_accounts / ask_signin_runs 的 domain 列值即本表 app_code,
--     join 关系: <表>.domain = ask_apps.app_code
--   * 只有"应用"进本表: 引擎/基础设施域(web_signin, schedule)是代码
--     形态不是应用,不建档(迁移会清掉历史误建的行)
--   * region 区分国内版/国际版: 国内版 app_code 带 _cn 后缀(如
--     doubao_cn / trae_cn),国际版裸名(qoder);真·双版本共存时注册表
--     域名加后缀(如 qoder_intl),本表各占一行
--   * 域的存在性跟代码走(注册表 + 域 YAML),本表只承载管理态与档案,
--     行由调用方惰性补齐;列表展示的档案字段(app_name/app_desc/kind/
--     region/credits_refresh_time/source)全部以本表为准,注册表/声明只是
--     补空的默认值来源
--   * credits_refresh_time 积分刷新时刻: 过了该时刻才算进入"新一天"
--     (done_today 调度跳过、"今日已签"提示按它算窗口起点)
-- 列序习惯: 主键 → 编码 → 展示档案 → 类别与版本区 → 积分刷新 → 开关 → 来源 → 审计。
CREATE TABLE IF NOT EXISTS ask_apps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    app_code    TEXT    NOT NULL,  -- 应用编码: 注册表域名,全库关联键(国内版带 _cn 后缀)
    app_name    TEXT,              -- 应用显示名(NULL/空 = 用注册表 DISPLAY_NAME/声明 display)
    app_desc    TEXT,              -- 应用描述/备注(NULL/空 = 用注册表 DESCRIPTION/声明 desc)
    kind        TEXT,              -- 类别: web(网页版签) / client(客户端签到)(NULL = 未登记)
    version     TEXT,              -- 客户端版本(如 qoder 0.4.8): 同步探测的权威值,有值即覆盖(NULL = 探测不到/声明式)
    region      TEXT    NOT NULL DEFAULT 'cn',  -- 版本区: cn / intl(国内版/国际版)
    credits_refresh_time TEXT,     -- 积分刷新时刻 HH:MM(NULL/空 = 00:00;Qoder 是 10:00,调度/提示按它判天)
    enabled     INTEGER NOT NULL DEFAULT 1,  -- 应用总开关(压在账户级 enabled 之上)
    source      TEXT    NOT NULL DEFAULT 'auto',  -- 行来源: auto(同步扫描) / manual(用户新建)
    -- 统一审计字段(所有表必备): 行惰性补齐创建,更新时由写层维护
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
    UNIQUE(app_code, region)
);
