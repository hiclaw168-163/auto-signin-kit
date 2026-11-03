"""台账 schema 与存量迁移(从 store.py 拆出,保持单文件行数合规)。

职责: 表/索引/视图 DDL、老库补列改名、存量行规整(时区抹平/结果词收敛/
request-response 回填)。store.py 只管读写,这里只管"库长什么样、怎么升级"。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

#: ask_accounts 建表 DDL 单独成常量: 建表(_SCHEMA)与列序重建(_migrate)共用,
#: 避免 DDL 双份漂移。
_ASK_ACCOUNTS_DDL = """
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
    UNIQUE(domain, name)
);
"""

#: ask_accounts 的目标列序(重要字段在前,打开表先见重点)。
_ACCOUNT_COLUMN_ORDER = ("id", "domain", "name", "device_id", "client_version",
                         "uid", "daily_potential", "enabled", "install_path",
                         "client_info", "info_synced_at")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ask_signin_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    executed_at     TEXT    NOT NULL,  -- 执行时间, 本地无时区 'YYYY-MM-DD HH:MM:SS'
                                       -- (时区不逐行带: 全库统一机器系统时区,
                                       --  旧版带 +08:00 后缀的行由迁移抹掉)
    domain          TEXT    NOT NULL,  -- workbuddy / qoder / trae
    action          TEXT,              -- 干了什么: auto / active / claim / backfill ...
    source          TEXT    NOT NULL DEFAULT 'run',  -- 数据哪来的: run / backfill
    result          TEXT    NOT NULL,  -- 标准词表 10 词(见 core/report.py RESULT_LABELS)
    needs_attention INTEGER NOT NULL DEFAULT 0,
    report          TEXT,              -- 一句人话摘要
    request         TEXT,              -- 请求参数 JSON(动作入参;与 response 一一对应)
    response        TEXT,              -- 响应结果 JSON(http 码/body/积分等业务返回)
    detail          TEXT    NOT NULL,  -- 完整事件 JSON(兼容保留,新读方用 request/response)
    account         TEXT    DEFAULT 'default'  -- 本次运行用的账户(积分业务主体维度)
);

CREATE TABLE IF NOT EXISTS ask_schedule_changes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    executed_at TEXT NOT NULL,  -- 变更时间, 本地无时区 'YYYY-MM-DD HH:MM:SS'
    action   TEXT NOT NULL,  -- install / uninstall
    platform TEXT,
    tasks    TEXT,           -- 本次变更涉及的任务与时间(JSON 数组)
    report   TEXT
);
""" + _ASK_ACCOUNTS_DDL

#: 依赖新列/新列名的索引与视图,必须在 _migrate 补列改名**之后**执行,
#: 不能进上面的建表脚本。历史沿革产生的三种旧索引名都在这里清掉
#: (无前缀版是最早的表名遗留),避免老库残留重复索引。
_SCHEMA_AFTER_MIGRATE = """
DROP INDEX IF EXISTS idx_signin_runs_ts;
DROP INDEX IF EXISTS idx_ask_signin_runs_ts;
CREATE INDEX IF NOT EXISTS idx_ask_signin_runs_executed_at
    ON ask_signin_runs(executed_at);
DROP INDEX IF EXISTS idx_signin_runs_domain_ts;
DROP INDEX IF EXISTS idx_ask_signin_runs_domain_ts;
CREATE INDEX IF NOT EXISTS idx_ask_signin_runs_domain_executed_at
    ON ask_signin_runs(domain, executed_at);
CREATE INDEX IF NOT EXISTS idx_ask_signin_runs_source ON ask_signin_runs(source);

-- 视图是纯派生物,每次连接重建(CREATE VIEW 无 IF NOT EXISTS 的改列能力,
-- 直接 DROP+CREATE 保证老库也能拿到新列)
DROP VIEW IF EXISTS ask_daily_checkins;
-- executed_at 已统一为本地无时区时间(迁移抹掉旧 +08:00 后缀),
-- 裸 date() 直接取日期部分,不再有 UTC 偏移问题
CREATE VIEW ask_daily_checkins AS
SELECT date(r.executed_at)   AS day,
       r.domain              AS domain,
       r.result              AS result,
       r.source              AS source,
       r.needs_attention     AS needs_attention,
       r.report              AS report,
       r.executed_at         AS executed_at
FROM ask_signin_runs AS r
WHERE r.id = (
    SELECT r2.id FROM ask_signin_runs AS r2
    WHERE date(r2.executed_at) = date(r.executed_at) AND r2.domain = r.domain
    ORDER BY r2.executed_at DESC, r2.id DESC
    LIMIT 1
);

-- 每日积分(纯派生视图,不落库——派生数据落库会引入两份真相):
-- 一行 = (日期, 域, 账户) 当天积分所得。两个口径要点:
-- ①字段方言收敛: qoder/trae 报 credits,workbuddy 的 CLAIMED 报 credit、
--   ALREADY 报 today_credit(当日已领快照),COALESCE 取首个非空;
--   workbuddy 的 score 是活跃度分、total_credits 是累计值,都绝不计入;
-- ②同域同天多行(高密度调度幂等重跑)按 MAX 取当天快照,不能 SUM 累加。
-- 结果词集合须与 core.report.RESULT_OK 同步(4 词,改动时两边一起改)。
DROP VIEW IF EXISTS ask_daily_credits;
CREATE VIEW ask_daily_credits AS
SELECT day, domain, account, MAX(credits_val) AS credits
FROM (
    SELECT date(executed_at) AS day,
           domain            AS domain,
           account           AS account,
           COALESCE(
               json_extract(response, '$.credits'),
               json_extract(response, '$.credit'),
               json_extract(response, '$.today_credit')
           ) AS credits_val
    FROM ask_signin_runs
    WHERE source = 'run'
      AND result IN ('CLAIMED', 'ALREADY', 'ACTIVE', 'OK')
)
WHERE credits_val IS NOT NULL
GROUP BY day, domain, account;
"""

#: 老库迁移: ALTER 加列后,把历史上靠 action 标记的回填行规整到 source 列;
#: 再补 request/response 两列(请求参数与响应结果配对落库)。
_MIGRATIONS = (
    ("source", "ALTER TABLE ask_signin_runs ADD COLUMN source TEXT NOT NULL DEFAULT 'run'",
     "UPDATE ask_signin_runs SET source = 'backfill' WHERE action = 'backfill'"),
    ("request", "ALTER TABLE ask_signin_runs ADD COLUMN request TEXT",
     None),
    ("response", "ALTER TABLE ask_signin_runs ADD COLUMN response TEXT",
     None),
    # account: 本次运行用的账户(当前每域一户 'default',一域多户时由域回传);
    # 存量行统一回填 default(视图按 account 分组,不能留 NULL)
    ("account", "ALTER TABLE ask_signin_runs ADD COLUMN account TEXT",
     "UPDATE ask_signin_runs SET account = 'default' WHERE account IS NULL"),
)

#: 存量行规整(幂等,每次 connect 跑): 旧版 executed_at 带 +08:00 时区后缀,
#: 裸 date() 会转 UTC;统一抹成本地无时区。结果词表 19 → 10 的历史值一并收敛。
_ROW_NORMALIZE_SQL = (
    "UPDATE ask_signin_runs SET executed_at = strftime('%Y-%m-%d %H:%M:%S',"
    " executed_at, 'localtime') WHERE executed_at LIKE '%+%';",
    "UPDATE ask_schedule_changes SET executed_at = strftime('%Y-%m-%d %H:%M:%S',"
    " executed_at, 'localtime') WHERE executed_at LIKE '%+%';",
    # 认证类 → NO_AUTH
    "UPDATE ask_signin_runs SET result='NO_AUTH'"
    " WHERE result IN ('NO_SESSION','AUTH_ERROR','AUTH_REJECTED','FORBIDDEN');",
    # 失败类收敛(未激活/限流/通用 ERROR)
    "UPDATE ask_signin_runs SET result='FAILED'"
    " WHERE result IN ('ERROR','FAIL','FAILURE','INACTIVE','RATE_LIMITED','LIMITED');",
    # 网络类收敛(超时是网络问题的一种)
    "UPDATE ask_signin_runs SET result='NETWORK'"
    " WHERE result IN ('TIMEOUT','TIMEOUT_ERROR','NETWORK_ERROR','NET_ERROR');",
    # 待生效 → OK;活动不在期 → 今日未活跃
    "UPDATE ask_signin_runs SET result='OK' WHERE result = 'PENDING';",
    "UPDATE ask_signin_runs SET result='NO_ACTIVITY' WHERE result = 'INACTIVE_ERROR';",
)

#: 事件信封字段——进独立列或表结构语义,不进 request/response。
ENVELOPE_FIELDS = frozenset({
    "ts", "level", "domain", "action", "source", "result",
    "needs_attention", "report", "request", "detail", "account",
})


def local_naive(ts: str) -> str:
    """ISO 时间戳 → 本地无时区 'YYYY-MM-DD HH:MM:SS'。

    时区不逐行落库: 全库统一机器系统时区(统一配置),带时区后缀只会让
    date() 查询在 UTC/本地之间反复踩坑(2026-11-03 凌晨调度"被消失"的根因)。
    """
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return str(ts)
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _split_record(record: dict) -> tuple[str | None, str | None]:
    """整行事件 → (request JSON, response JSON);信封字段留独立列。"""
    request = record.get("request") or {}
    response = {k: v for k, v in record.items() if k not in ENVELOPE_FIELDS}
    return (
        json.dumps(request, ensure_ascii=False) if request else None,
        json.dumps(response, ensure_ascii=False) if response else None,
    )


def _reorder_ask_accounts(conn: sqlite3.Connection) -> None:
    """ask_accounts 实际列序与目标不一致(历史 ALTER 追加导致重点列靠后)
    时整表重建搬数据;一致则跳过(幂等)。

    SQLite 不支持调整列序,只能改名→按目标 DDL 重建→按名列拷贝→删旧。
    AUTOINCREMENT 的 sqlite_sequence 随显式 id 插入与 RENAME 自动跟随,
    重建不丢自增计数;UNIQUE(domain,name) 由目标 DDL 原样带上。
    """
    actual = tuple(
        row["name"] for row in conn.execute("PRAGMA table_info(ask_accounts)"))
    if actual == _ACCOUNT_COLUMN_ORDER:
        return
    column_list = ", ".join(_ACCOUNT_COLUMN_ORDER)
    with conn:  # 整表重建单事务: 中断则旧表还在,下次连接重来
        conn.execute("ALTER TABLE ask_accounts RENAME TO ask_accounts_old")
        conn.execute(_ASK_ACCOUNTS_DDL)  # 旧表已改名,IF NOT EXISTS 直接建新
        # S608 豁免: 列名来自模块级目标序常量,无外部输入(列清单不可参数化)
        conn.execute(
            "INSERT INTO ask_accounts (%s) SELECT %s FROM ask_accounts_old"  # noqa: S608
            % (column_list, column_list))
        conn.execute("DROP TABLE ask_accounts_old")


def _migrate(conn: sqlite3.Connection) -> None:
    """老库补列/改名 + 存量行规整(全部幂等): CREATE TABLE IF NOT EXISTS 不会动已有表。"""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(ask_signin_runs)")}
    # ts → executed_at: 字段名见名知义(值不变,仍是 ISO8601 时间戳);
    # RENAME COLUMN 会自动更新引用该列的索引定义
    if "ts" in columns and "executed_at" not in columns:
        with conn:
            conn.execute("ALTER TABLE ask_signin_runs RENAME COLUMN ts TO executed_at")
    sched_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(ask_schedule_changes)")
    }
    if "ts" in sched_columns and "executed_at" not in sched_columns:
        with conn:
            conn.execute("ALTER TABLE ask_schedule_changes RENAME COLUMN ts TO executed_at")
    # ask_accounts 档案列: 早期建表(2026-11-03 前)没有这些列,逐列补上(幂等);
    # _SCHEMA 先行执行,表到这里必然已存在
    acct_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ask_accounts)")}
    for col in ("client_info", "info_synced_at", "client_version", "device_id",
                "install_path", "uid"):
        if col not in acct_columns:
            with conn:
                conn.execute("ALTER TABLE ask_accounts ADD COLUMN %s TEXT" % col)
    _reorder_ask_accounts(conn)
    for name, add_sql, backfill_sql in _MIGRATIONS:
        if name not in columns:
            with conn:
                conn.execute(add_sql)
                if backfill_sql:
                    conn.execute(backfill_sql)
    # 存量行规整(幂等): 时区抹平 + 旧结果词收敛;放在视图重建之前,
    # 视图的 date() 才能按"本地裸时间"正确取日期
    for sql in _ROW_NORMALIZE_SQL:
        conn.execute(sql)
    # 老行回填 request/response(从 detail JSON 拆列;幂等,只补空行)
    stale = conn.execute(
        "SELECT id, detail FROM ask_signin_runs"
        " WHERE response IS NULL AND detail IS NOT NULL").fetchall()
    updates = []
    for row in stale:
        try:
            record = json.loads(row["detail"])
        except (TypeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        request, response = _split_record(record)
        updates.append((request, response, row["id"]))
    if updates:
        with conn:
            conn.executemany(
                "UPDATE ask_signin_runs SET request = ?, response = ? WHERE id = ?",
                updates)
