"""签到运行台账(``ask_signin_runs`` + ``ask_daily_checkins`` 视图)的读查与维护。

查询口径约定(两版本文档均在此):
- ``source`` = **数据哪来的**——``run`` 正常执行 / ``backfill`` 回填;
  "只看真实执行" ``WHERE source='run'``,一条 SQL 的事;
- ``action`` = **干了什么**——正常执行是当时的命令,回填行统一 ``backfill``。

不建更多表的理由: 连签天数、积分累计这类"统计"都存在 ``detail`` 的完整事件
JSON 里(SQLite 内置 json_extract 可查),存派生数据只会引入两份真相——
``overview_stats`` 现算不落库正是这个原则。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from auto_signin_kit.core.report import RESULT_OK, normalize_result
from auto_signin_kit.core.store.connection import connect

#: 台账覆盖的签到域(Web 总览按它补齐"还没有任何记录"的域卡片)。
#: doubao 与其他域同走 emit_result 落库,纳入正式名单。
SIGNIN_DOMAINS = ("workbuddy", "qoder", "trae", "doubao")

#: 算"当天已成功签到"的结果词(签到语义的成功;ACTIVE/OK 是相邻语义,不算)。
SIGNIN_DONE_RESULTS = ("CLAIMED", "ALREADY")


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
    - ``credits`` / ``today_credits``: 成功行 response 里的每日积分所得
      (口径与 ``ask_daily_credits`` 视图一致: 字段方言 COALESCE + 按日取 MAX,
      域不回传该字段时按 0 计,如 doubao)。
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
    day_credits: dict[tuple[str, str], int] = {}
    for row in rows:
        d = per.setdefault(row["domain"], {
            "ok_days": set(), "last": None})
        if row["result"] in RESULT_OK:
            d["ok_days"].add(row["day"])
            earned = _extract_credits(row["response"])
            # 同域同天多行(幂等重跑)取最大快照,不累加——响应里是"当日所得"
            key = (row["domain"], row["day"])
            day_credits[key] = max(day_credits.get(key, 0), earned)
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
        credits = sum(v for (dom, _day), v in day_credits.items() if dom == domain)
        out[domain] = {"streak": streak, "days14": days14, "credits": credits,
                       "today_credits": day_credits.get((domain, today), 0),
                       "last_run": d["last"]}
    return out


#: 积分字段方言(按优先级);须与 ask_daily_credits 视图的 COALESCE 同步。
_CREDIT_FIELD_DIALECTS = ("credits", "credit", "today_credit")


def _extract_credits(response: str | None) -> int:
    """成功行 response 里的每日积分(方言 COALESCE);缺字段/解析失败按 0。

    score(活跃度分)与 total_credits(累计值)都不算——那是另外两个量纲。
    """
    if not response:
        return 0
    try:
        data = json.loads(response)
    except (TypeError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    for field in _CREDIT_FIELD_DIALECTS:
        value = data.get(field)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


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
