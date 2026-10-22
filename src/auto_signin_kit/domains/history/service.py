"""history 域业务逻辑: 台账查询与历史日志回填(纯逻辑,不碰 CLI)。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_signin_kit.core.adapters.log_dir import default_log_dir
from auto_signin_kit.core.store import connect

#: 台账覆盖的签到域;回填时按这个名字找对应日志文件。
SIGNIN_DOMAINS = ("workbuddy", "qoder", "trae")

#: detail JSON 里值得在台账视图中单列的汇总字段(按域可能缺省,查不到就不显示)。
_SUMMARY_KEYS = ("credits", "streak_days", "total_credits", "score")


def daily_status(days: int = 7, *, domain: str | None = None) -> list[dict[str, Any]]:
    """最近 N 天每天每域的最终状态(读 ``v_daily_checkins`` 视图),新的一天在前。"""
    conn = connect()
    try:
        sql = (
            "SELECT day, domain, result, needs_attention, report, last_run"
            " FROM v_daily_checkins WHERE day >= date('now', ?)"
        )
        params: list[Any] = ["-%d days" % max(1, days)]
        if domain:
            sql += " AND domain = ?"
            params.append(domain)
        rows = conn.execute(sql + " ORDER BY day DESC, domain", params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def events(
    *,
    date: str | None = None,
    domain: str | None = None,
    attention_only: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """按条件查 ``signin_runs`` 明细,最新在前。"""
    sql = "SELECT ts, domain, action, result, needs_attention, report FROM signin_runs WHERE 1=1"
    params: list[Any] = []
    if date:
        sql += " AND date(ts) = ?"
        params.append(date)
    if domain:
        sql += " AND domain = ?"
        params.append(domain)
    if attention_only:
        sql += " AND needs_attention = 1"
    sql += " ORDER BY ts DESC, id DESC LIMIT ?"
    params.append(limit)
    conn = connect()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def summary_of(detail_json: str) -> str:
    """从 detail 事件 JSON 里提取台账视图关注的汇总字段(有才显示)。"""
    try:
        detail = json.loads(detail_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    parts = ["%s=%s" % (key, detail[key]) for key in _SUMMARY_KEYS if detail.get(key) is not None]
    return " ".join(parts)


def import_logs(log_dir: Path | None = None) -> dict[str, Any]:
    """把现有 JSONL 日志回填进台账;幂等,重复导入按 (ts,domain,result,report) 去重。

    同时容忍两种行格式: 新版 JSONL(行内带 ``ts``)与旧版
    ``[YYYY-MM-DD HH:MM:SS] {json}``(时间戳在行首方括号里)。
    """
    root = log_dir if log_dir is not None else default_log_dir()
    imported = skipped = 0
    per_domain: dict[str, int] = {}
    conn = connect()
    try:
        for domain in SIGNIN_DOMAINS:
            log_file = root / ("%s.log" % domain)
            if not log_file.exists():
                continue
            count = 0
            for line in log_file.read_text(encoding="utf-8").splitlines():
                record = _parse_line(line, domain)
                if record is None:
                    continue
                if _already_exists(conn, record):
                    skipped += 1
                    continue
                with conn:
                    conn.execute(
                        "INSERT INTO signin_runs (ts, domain, action, result,"
                        " needs_attention, report, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            record["ts"], record["domain"], record.get("action"),
                            record["result"], 1 if record.get("needs_attention") else 0,
                            record.get("report"), record["detail"],
                        ),
                    )
                count += 1
            per_domain[domain] = count
            imported += count
    finally:
        conn.close()
    return {"imported": imported, "skipped_duplicates": skipped, "per_domain": per_domain}


def _parse_line(line: str, domain: str) -> dict[str, Any] | None:
    """解析一行日志;新旧两种格式都认,解析不了的行静默跳过(回填不中断)。"""
    line = line.strip()
    if not line:
        return None
    if line.startswith("[") and "]" in line:
        stamp, _, payload = line.partition("] ")
        try:
            moment = datetime.fromisoformat(stamp.strip("[]").replace(" ", "T"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.astimezone()
        try:
            record = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if isinstance(record, dict):
            record["ts"] = moment.isoformat(timespec="seconds")
    else:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return None
    if not isinstance(record, dict) or not record.get("ts") or not record.get("result"):
        return None
    record.setdefault("domain", domain)
    record["domain"] = domain  # 以文件归属为准,防旧日志缺 domain 字段
    record["detail"] = json.dumps(record, ensure_ascii=False)
    return record


def _already_exists(conn: sqlite3.Connection, record: dict[str, Any]) -> bool:
    row = conn.execute(
        "SELECT 1 FROM signin_runs WHERE ts = ? AND domain = ? AND result = ?"
        " AND report IS ? LIMIT 1",
        (record["ts"], record["domain"], record["result"], record.get("report")),
    ).fetchone()
    return row is not None
