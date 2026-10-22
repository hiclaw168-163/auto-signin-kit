"""SQLite 台账层: 签到结果与调度变更的规整落库。

表设计(统一 ``ask_`` 前缀 = auto-signin-kit,同类对象一眼归属;
刻意不用 ``events`` 之类笼统大表,每张表见名知义):

- ``ask_signin_runs``      **签到运行台账**: 每次签到/领取/活跃保底命令的运行结果,
                           一行一次运行,是"这周有没有断签"这类问题的数据源
- ``ask_schedule_changes`` **调度变更审计**: 定时任务的注册/注销动作记录,
                           回答"任务是什么时候被谁改成的什么时间"
- ``ask_daily_checkins``   **视图**(非实体表): 每天每域最后一次运行 = 当天最终状态,
                           把"一天多次运行"折叠成一行,history 命令与人工查库都读它

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

#: 台账文件名(运行时数据目录,与日志分居;同样被 .gitignore 排除)。
LEDGER_FILENAME = "auto-signin-kit.db"

#: 调度域的结果不进签到台账,单独记审计表。
SCHEDULE_DOMAIN = "schedule"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ask_signin_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,  -- ISO8601 本地时区
    domain          TEXT    NOT NULL,  -- workbuddy / qoder / trae
    action          TEXT,              -- 干了什么: auto / active / claim / backfill ...
    source          TEXT    NOT NULL DEFAULT 'run',  -- 数据哪来的: run / backfill
    result          TEXT    NOT NULL,  -- CLAIMED / ALREADY / ACTIVE / NO_AUTH ...
    needs_attention INTEGER NOT NULL DEFAULT 0,
    report          TEXT,              -- 一句人话摘要
    detail          TEXT    NOT NULL   -- 完整事件 JSON(积分/连签等全部字段)
);
CREATE INDEX IF NOT EXISTS idx_ask_signin_runs_ts ON ask_signin_runs(ts);
CREATE INDEX IF NOT EXISTS idx_ask_signin_runs_domain_ts ON ask_signin_runs(domain, ts);

CREATE TABLE IF NOT EXISTS ask_schedule_changes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    action   TEXT NOT NULL,  -- install / uninstall
    platform TEXT,
    tasks    TEXT,           -- 本次变更涉及的任务与时间(JSON 数组)
    report   TEXT
);
"""

#: 依赖新列的索引与视图,必须在 _migrate 补列**之后**执行,不能进上面的建表脚本。
_SCHEMA_AFTER_MIGRATE = """
CREATE INDEX IF NOT EXISTS idx_ask_signin_runs_source ON ask_signin_runs(source);

-- 视图是纯派生物,每次连接重建(CREATE VIEW 无 IF NOT EXISTS 的改列能力,
-- 直接 DROP+CREATE 保证老库也能拿到新列)
DROP VIEW IF EXISTS ask_daily_checkins;
CREATE VIEW ask_daily_checkins AS
SELECT date(r.ts)            AS day,
       r.domain              AS domain,
       r.result              AS result,
       r.source              AS source,
       r.needs_attention     AS needs_attention,
       r.report              AS report,
       r.ts                  AS last_run
FROM ask_signin_runs AS r
WHERE r.id = (
    SELECT r2.id FROM ask_signin_runs AS r2
    WHERE date(r2.ts) = date(r.ts) AND r2.domain = r.domain
    ORDER BY r2.ts DESC, r2.id DESC
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
    conn.executescript(_SCHEMA_AFTER_MIGRATE)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """老库补列(幂等): CREATE TABLE IF NOT EXISTS 不会给已有表加列。"""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(ask_signin_runs)")}
    for name, add_sql, backfill_sql in _MIGRATIONS:
        if name not in columns:
            with conn:
                conn.execute(add_sql)
                conn.execute(backfill_sql)


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
                        "INSERT INTO ask_schedule_changes (ts, action, platform, tasks, report)"
                        " VALUES (?, ?, ?, ?, ?)",
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
                        "INSERT INTO ask_signin_runs (ts, domain, action, source,"
                        " result, needs_attention, report, detail)"
                        " VALUES (?, ?, ?, 'run', ?, ?, ?, ?)",
                        (
                            record["ts"],
                            str(record.get("domain") or "unknown"),
                            record.get("action"),
                            str(record.get("result") or "UNKNOWN"),
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
