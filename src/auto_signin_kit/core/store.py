"""SQLite 台账层: 签到结果与调度变更的规整落库。

表设计(统一 ``ask_`` 前缀 = auto-signin-kit,同类对象一眼归属;
刻意不用 ``events`` 之类笼统大表,每张表见名知义):

- ``ask_signin_runs``      **签到运行台账**: 每次签到/领取/活跃保底命令的运行结果,
                           一行一次运行,是"这周有没有断签"这类问题的数据源
- ``ask_schedule_changes`` **调度变更审计**: 定时任务的注册/注销动作记录,
                           回答"任务是什么时候被谁改成的什么时间"
- ``ask_daily_checkins``   **视图**(非实体表): 每天每域最后一次运行 = 当天最终状态,
                           把"一天多次运行"折叠成一行,Web 台账页与人工查库都读它

两个标识列的分工(查数据先想清楚用哪个):

- ``source`` = **数据哪来的**——``run`` 正常执行(emit_result 落库) /
  ``backfill`` 回填(import-logs / import-server)。细分来源在 ``detail.source``
  (``log-import`` 日志导入 / ``server-backfill`` 服务端导入)。
  "只看真实执行" ``WHERE source='run'``,一条 SQL 的事。
- ``action``  = **干了什么**——正常执行是当时的命令(auto / active / claim…),
  回填行统一 ``backfill``。旧数据(2026-10-22 前)正常行可能缺 action。

不建更多表的理由: 连签天数、积分累计这类"统计"都存在 ``detail`` 的完整事件
JSON 里(SQLite 内置 json_extract 可查),存派生数据只会引入两份真相。

写入是 **best-effort**: 台账永远不能影响签到本身,任何数据库错误都在本层
静默吞掉。落库的内容是 emit_result 脱敏后的 JSON 行——令牌在进库前就已
替换为 ``[REDACTED]``,台账里不会有敏感值。

并发: 多个定时任务可能同时写,连接开 WAL + 3s busy_timeout;单行短事务,
够用且无需引入任何外部依赖(标准库 sqlite3)。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from auto_signin_kit.core.adapters.log_dir import default_data_dir
from auto_signin_kit.core.report import normalize_result

#: 台账文件名(运行时数据目录,与日志分居;同样被 .gitignore 排除)。
LEDGER_FILENAME = "auto-signin-kit.db"

#: 调度域的结果不进签到台账,单独记审计表。
SCHEDULE_DOMAIN = "schedule"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ask_signin_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    executed_at     TEXT    NOT NULL,  -- 执行时间, ISO8601 带时区(旧名 ts,已迁移)
    domain          TEXT    NOT NULL,  -- workbuddy / qoder / trae
    action          TEXT,              -- 干了什么: auto / active / claim / backfill ...
    source          TEXT    NOT NULL DEFAULT 'run',  -- 数据哪来的: run / backfill
    result          TEXT    NOT NULL,  -- CLAIMED / ALREADY / ACTIVE / NO_AUTH ...
    needs_attention INTEGER NOT NULL DEFAULT 0,
    report          TEXT,              -- 一句人话摘要
    detail          TEXT    NOT NULL   -- 完整事件 JSON(积分/连签等全部字段)
);

CREATE TABLE IF NOT EXISTS ask_schedule_changes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    executed_at TEXT NOT NULL,  -- 变更时间, ISO8601 带时区(旧名 ts,已迁移)
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
-- 日期一律 date(..., 'localtime'): executed_at 带时区后缀,裸 date() 会转成
-- UTC 日期——本地 0~8 点的运行会被记到"昨天",台账看起来就像调度没跑
CREATE VIEW ask_daily_checkins AS
SELECT date(r.executed_at, 'localtime') AS day,
       r.domain              AS domain,
       r.result              AS result,
       r.source              AS source,
       r.needs_attention     AS needs_attention,
       r.report              AS report,
       r.executed_at         AS executed_at
FROM ask_signin_runs AS r
WHERE r.id = (
    SELECT r2.id FROM ask_signin_runs AS r2
    WHERE date(r2.executed_at, 'localtime') = date(r.executed_at, 'localtime')
      AND r2.domain = r.domain
    ORDER BY r2.executed_at DESC, r2.id DESC
    LIMIT 1
);
"""

#: 老库迁移: ALTER 加列后,把历史上靠 action 标记的回填行规整到 source 列。
_MIGRATIONS = (
    ("source", "ALTER TABLE ask_signin_runs ADD COLUMN source TEXT NOT NULL DEFAULT 'run'",
     "UPDATE ask_signin_runs SET source = 'backfill' WHERE action = 'backfill'"),
)


def db_path() -> Any:
    """台账文件路径: 运行时数据目录(尊重 ``AUTO_SIGNIN_KIT_DATA_DIR``)。"""
    return default_data_dir() / LEDGER_FILENAME


def connect() -> sqlite3.Connection:
    """打开台账连接并确保表结构存在(含老库加列迁移);调用方负责 close。

    顺序有讲究: 建表 → 老库补列 → 建依赖新列的索引/视图,
    否则老库上视图/索引引用尚不存在的列会直接报错。
    """
    conn = sqlite3.connect(db_path(), timeout=3.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    # 视图重建(DROP+CREATE)必须原子: 多进程并发 connect(CLI 落库 vs UI 服务)时,
    # 两个非事务的 executescript 交错会在 CREATE VIEW 撞 "already exists"。
    # BEGIN IMMEDIATE 拿写锁把整个脚本串行化,抢不到锁的等 busy_timeout。
    conn.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA_AFTER_MIGRATE + "\nCOMMIT;")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """老库补列/改名(幂等): CREATE TABLE IF NOT EXISTS 不会动已有表。"""
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
                conn.execute(backfill_sql)


#: 台账覆盖的签到域(Web 总览按它补齐"还没有任何记录"的域卡片)。
#: doubao 与其他域同走 emit_result 落库,纳入正式名单。
SIGNIN_DOMAINS = ("workbuddy", "qoder", "trae", "doubao")


def daily_status(days: int = 7, *, domain: str | None = None,
                 source: str | None = None,
                 result: str | None = None) -> list[dict[str, Any]]:
    """最近 N 天每天每域的最终状态(读 ``ask_daily_checkins`` 视图),新的一天在前。"""
    conn = connect()
    try:
        sql = (
            "SELECT day, domain, result, source, needs_attention, report, executed_at"
            " FROM ask_daily_checkins WHERE day >= date('now', 'localtime', ?)"
        )
        params: list[Any] = ["-%d days" % max(1, days)]
        if domain:
            sql += " AND domain = ?"
            params.append(domain)
        if source:
            sql += " AND source = ?"
            params.append(source)
        if result:
            sql += " AND result = ?"
            params.append(normalize_result(result))
        rows = conn.execute(sql + " ORDER BY day DESC, domain", params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def events(
    *,
    date: str | None = None,
    domain: str | None = None,
    attention_only: bool = False,
    source: str | None = None,
    result: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """按条件查 ``ask_signin_runs`` 明细,最新在前;``source`` 过滤 run/backfill。"""
    sql = ("SELECT executed_at, domain, action, source, result, needs_attention, report"
           " FROM ask_signin_runs WHERE 1=1")
    params: list[Any] = []
    if date:
        sql += " AND date(executed_at, 'localtime') = ?"
        params.append(date)
    if domain:
        sql += " AND domain = ?"
        params.append(domain)
    if source:
        sql += " AND source = ?"
        params.append(source)
    if result:
        sql += " AND result = ?"
        params.append(normalize_result(result))
    if attention_only:
        sql += " AND needs_attention = 1"
    sql += " ORDER BY executed_at DESC, id DESC LIMIT ?"
    params.append(limit)
    conn = connect()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


#: 算"当天已成功签到"的结果词(签到语义的成功;ACTIVE/OK 是相邻语义,不算)。
SIGNIN_DONE_RESULTS = ("CLAIMED", "ALREADY")


def done_today(domain: str) -> bool:
    """该域今天(source=run)是否已有成功签到行;调度兜底触发的跳过判断用。

    时间口径与 ``ask_daily_checkins`` 视图一致: ``executed_at`` 存带时区的
    ISO 时间戳,左侧 ``date(executed_at, 'localtime')`` 取其**本地**日期
    (裸 date() 会转 UTC,凌晨 0~8 点的运行会被算到昨天);右侧
    ``date('now', 'localtime')`` 得到今天本地日期。
    """
    conn = connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM ask_signin_runs WHERE domain = ? AND source = 'run'"
            " AND result IN (?, ?)"
            " AND date(executed_at, 'localtime') = date('now', 'localtime')"
            " LIMIT 1", (domain, *SIGNIN_DONE_RESULTS)).fetchone()
    finally:
        conn.close()
    return row is not None


def compact_history() -> dict[str, Any]:
    """清理高密度调度留下的历史冗余成功行;返回 {"deleted", "per_domain"}。

    每天 (day, domain) 只保留: **首条成功行**(真正完成签到的那次)与**当天
    最后一行**(``ask_daily_checkins`` 视图的最终状态取它);失败/需关注行与
    backfill 行一律不动。幂等——清完再跑 deleted=0。
    """
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, domain FROM ask_signin_runs"
            " WHERE source = 'run' AND result IN ('CLAIMED', 'ALREADY')"
            "   AND id NOT IN (SELECT MIN(id) FROM ask_signin_runs"
            "                  WHERE source = 'run' AND result IN ('CLAIMED', 'ALREADY')"
            "                  GROUP BY date(executed_at, 'localtime'), domain)"
            "   AND id NOT IN (SELECT MAX(id) FROM ask_signin_runs"
            "                  GROUP BY date(executed_at, 'localtime'), domain)"
        ).fetchall()
        per_domain: dict[str, int] = {}
        ids = []
        for row in rows:
            per_domain[row["domain"]] = per_domain.get(row["domain"], 0) + 1
            ids.append((row["id"],))
        with conn:
            conn.executemany("DELETE FROM ask_signin_runs WHERE id = ?", ids)
    finally:
        conn.close()
    return {"deleted": len(ids), "per_domain": per_domain}


def record_payload(payload: str) -> bool:
    """把 emit_result 脱敏后的一行 JSON 事件落库;失败返回 False,绝不抛。

    按域分流: 调度域进 ``ask_schedule_changes``,签到域进 ``ask_signin_runs``。
    """
    try:
        record = json.loads(payload)
        if not isinstance(record, dict) or not record.get("ts"):
            return False
        conn = connect()
        try:
            with conn:
                if record.get("domain") == SCHEDULE_DOMAIN:
                    conn.execute(
                        "INSERT INTO ask_schedule_changes (executed_at, action, platform,"
                        " tasks, report) VALUES (?, ?, ?, ?, ?)",
                        (
                            record["ts"],
                            str(record.get("action") or "unknown"),
                            record.get("platform"),
                            json.dumps(
                                {"done": record.get("done"), "failed": record.get("failed")},
                                ensure_ascii=False,
                            ),
                            record.get("report"),
                        ),
                    )
                else:
                    conn.execute(
                        "INSERT INTO ask_signin_runs (executed_at, domain, action, source,"
                        " result, needs_attention, report, detail)"
                        " VALUES (?, ?, ?, 'run', ?, ?, ?, ?)",
                        (
                            record["ts"],
                            str(record.get("domain") or "unknown"),
                            record.get("action"),
                            # 归一化到标准词表: 各域"方言"(大小写/别名)进库前收口,
                            # 查询与聚合才有统一口径
                            normalize_result(record.get("result")),
                            1 if record.get("needs_attention") else 0,
                            record.get("report"),
                            payload,
                        ),
                    )
        finally:
            conn.close()
        return True
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return False
