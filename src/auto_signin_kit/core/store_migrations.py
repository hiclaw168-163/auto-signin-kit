"""台账 schema 与存量迁移(从 store.py 拆出,保持单文件行数合规)。

职责: 表/索引/视图 DDL、老库补列改名、存量行规整(时区抹平/结果词收敛/
request-response 回填)。store.py 只管读写,这里只管"库长什么样、怎么升级"。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

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
    detail          TEXT    NOT NULL   -- 完整事件 JSON(兼容保留,新读方用 request/response)
);

CREATE TABLE IF NOT EXISTS ask_schedule_changes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    executed_at TEXT NOT NULL,  -- 变更时间, 本地无时区 'YYYY-MM-DD HH:MM:SS'
    action   TEXT NOT NULL,  -- install / uninstall
    platform TEXT,
    tasks    TEXT,           -- 本次变更涉及的任务与时间(JSON 数组)
    report   TEXT
);
"""

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
    "needs_attention", "report", "request", "detail",
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
