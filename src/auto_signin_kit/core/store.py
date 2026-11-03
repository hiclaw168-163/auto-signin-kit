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
from datetime import date, timedelta
from typing import Any

from auto_signin_kit.core.adapters.log_dir import default_data_dir
from auto_signin_kit.core.report import RESULT_OK, normalize_result
from auto_signin_kit.core.store_migrations import (
    _SCHEMA,
    _SCHEMA_AFTER_MIGRATE,
    _migrate,
    _split_record,
    local_naive,
)

#: 台账文件名(运行时数据目录,与日志分居;同样被 .gitignore 排除)。
LEDGER_FILENAME = "auto-signin-kit.db"

#: 调度域的结果不进签到台账,单独记审计表。
SCHEDULE_DOMAIN = "schedule"


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


def overview_stats() -> dict[str, dict[str, Any]]:
    """每域汇总统计(读台账,现算不落库): 14 天逐日成败/连续签到天数/积分/最近运行。

    - ``days14``: 今天往前 14 天的逐日布尔(当天有任一成功行即 OK),供色块条;
    - ``streak``: 截至今天的连续成功天数(今天还没签则从昨天起算,不把"还没到"
      当成断签);
    - ``credits`` / ``today_credits``: 成功行 ``response`` JSON 里 ``credits``
      字段的累计/今日累计(域不回传该字段时按 0 计,如 doubao)。
    """
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT date(executed_at) AS day, domain, result, executed_at, response"
            " FROM ask_signin_runs WHERE source = 'run' ORDER BY executed_at"
        ).fetchall()
    finally:
        conn.close()
    today = date.today().isoformat()
    per: dict[str, dict[str, Any]] = {}
    for row in rows:
        d = per.setdefault(row["domain"], {
            "ok_days": set(), "last": None, "credits": 0, "today_credits": 0})
        if row["result"] in RESULT_OK:
            d["ok_days"].add(row["day"])
            earned = _extract_credits(row["response"])
            d["credits"] += earned
            if row["day"] == today:
                d["today_credits"] += earned
        d["last"] = row["executed_at"]
    out: dict[str, dict[str, Any]] = {}
    today_date = date.fromisoformat(today)
    for domain, d in per.items():
        # 今天没签从昨天起算: "还没到"不算断签
        cursor = today_date if today in d["ok_days"] else today_date - timedelta(days=1)
        streak = 0
        while cursor.isoformat() in d["ok_days"]:
            streak += 1
            cursor -= timedelta(days=1)
        days14 = [{"day": (today_date - timedelta(days=13 - i)).isoformat(),
                   "ok": (today_date - timedelta(days=13 - i)).isoformat() in d["ok_days"]}
                  for i in range(14)]
        out[domain] = {"streak": streak, "days14": days14, "credits": d["credits"],
                       "today_credits": d["today_credits"], "last_run": d["last"]}
    return out


def _extract_credits(response: str | None) -> int:
    """成功行 response JSON 里的 ``credits`` 整数;缺字段/解析失败按 0。"""
    if not response:
        return 0
    try:
        value = json.loads(response).get("credits")
    except (TypeError, ValueError):
        return 0
    return value if isinstance(value, (int, float)) else 0


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
    sql = ("SELECT executed_at, domain, action, source, result, needs_attention, report,"
           " request, response"
           " FROM ask_signin_runs WHERE 1=1")
    params: list[Any] = []
    if date:
        sql += " AND date(executed_at) = ?"
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
            " AND date(executed_at) = date('now', 'localtime')"
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
            "                  GROUP BY date(executed_at), domain)"
            "   AND id NOT IN (SELECT MAX(id) FROM ask_signin_runs"
            "                  GROUP BY date(executed_at), domain)"
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


def db_path() -> Any:
    """台账文件路径: 运行时数据目录(尊重 ``AUTO_SIGNIN_KIT_DATA_DIR``)。"""
    return default_data_dir() / LEDGER_FILENAME


def connect() -> sqlite3.Connection:
    """打开台账连接并确保表结构存在(含老库加列迁移);调用方负责 close。

    顺序有讲究: 建表 → 老库补列/存量规整 → 建依赖新列的索引/视图,
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


def record_payload(payload: str) -> bool:
    """把 emit_result 脱敏后的一行 JSON 事件落库;失败返回 False,绝不抛。

    按域分流: 调度域进 ``ask_schedule_changes``,签到域进 ``ask_signin_runs``。
    请求/响应拆列: ``request`` 收动作入参(emit_result 的 request= 注入),
    ``response`` 收其余业务返回字段,两列一一对应;``detail`` 保留整行兼容。
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
                            local_naive(record["ts"]),
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
                    request, response = _split_record(record)
                    conn.execute(
                        "INSERT INTO ask_signin_runs (executed_at, domain, action, source,"
                        " result, needs_attention, report, request, response, detail)"
                        " VALUES (?, ?, ?, 'run', ?, ?, ?, ?, ?, ?)",
                        (
                            local_naive(record["ts"]),
                            str(record.get("domain") or "unknown"),
                            record.get("action"),
                            # 归一化到标准词表: 域内部细粒度码(大小写/别名)进库
                            # 前收口成 10 词,查询与聚合才有统一口径
                            normalize_result(record.get("result")),
                            1 if record.get("needs_attention") else 0,
                            record.get("report"),
                            request,
                            response,
                            payload,
                        ),
                    )
        finally:
            conn.close()
        return True
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return False
