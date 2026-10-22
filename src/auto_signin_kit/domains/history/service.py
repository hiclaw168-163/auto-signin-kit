"""history 域业务逻辑: 台账查询、日志回填与服务端历史回填(纯逻辑,不碰 CLI)。

服务端回填(``server_backfill``)是本域唯一跨域 import 的地方——它读各域的
接口层取"服务端还能查到的历史"(workbuddy 活跃日历 / qoder 活动领取记录),
已按规矩在 pyproject 的 ignore_imports 里显式登记。trae 没有历史接口
(只有当日状态),诚实跳过,不伪造。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auto_signin_kit.core.adapters.log_dir import default_log_dir
from auto_signin_kit.core.store import connect

#: 台账覆盖的签到域;回填时按这个名字找对应日志文件。
SIGNIN_DOMAINS = ("workbuddy", "qoder", "trae")

#: detail JSON 里值得在台账视图中单列的汇总字段(按域可能缺省,查不到就不显示)。
_SUMMARY_KEYS = ("credits", "streak_days", "total_credits", "score")


def daily_status(days: int = 7, *, domain: str | None = None,
                 source: str | None = None) -> list[dict[str, Any]]:
    """最近 N 天每天每域的最终状态(读 ``ask_daily_checkins`` 视图),新的一天在前。"""
    conn = connect()
    try:
        sql = (
            "SELECT day, domain, result, source, needs_attention, report, executed_at"
            " FROM ask_daily_checkins WHERE day >= date('now', ?)"
        )
        params: list[Any] = ["-%d days" % max(1, days)]
        if domain:
            sql += " AND domain = ?"
            params.append(domain)
        if source:
            sql += " AND source = ?"
            params.append(source)
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
    limit: int = 50,
) -> list[dict[str, Any]]:
    """按条件查 ``ask_signin_runs`` 明细,最新在前;``source`` 过滤 run/backfill。"""
    sql = ("SELECT executed_at, domain, action, source, result, needs_attention, report"
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
                        "INSERT INTO ask_signin_runs (executed_at, domain, action, source,"
                        " result, needs_attention, report, detail)"
                        " VALUES (?, ?, ?, 'backfill', ?, ?, ?, ?)",
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


def _parse_line(line: str, domain: str,
                source: str = "log-import") -> dict[str, Any] | None:
    """解析一行日志;新旧两种格式都认,解析不了的行静默跳过(回填不中断)。

    ``source`` 注进事件 JSON(即台账 ``detail``),细分回填来源。
    """
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
    record["source"] = source
    record["detail"] = json.dumps(record, ensure_ascii=False)
    return record


def _already_exists(conn: sqlite3.Connection, record: dict[str, Any]) -> bool:
    row = conn.execute(
        "SELECT 1 FROM ask_signin_runs WHERE executed_at = ? AND domain = ? AND result = ?"
        " AND report IS ? LIMIT 1",
        (record["ts"], record["domain"], record["result"], record.get("report")),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# 服务端历史回填: 服务端还查得到的历史,整理进台账
# ---------------------------------------------------------------------------

#: 回填行的固定时刻(本地): 同一天重复回填生成同一 ts,去重键天然幂等。
_BACKFILL_TIME = "T12:00:00+08:00"


def server_backfill() -> dict[str, Any]:
    """把服务端还能查到的历史整理进台账;查不到的域如实报告原因。

    - workbuddy: 活跃日历(heatmap)有本月的逐日 score —— 连续活跃天数的
      事实源,逐日回填(ACTIVE/NO_ACTIVITY)
    - qoder: 活动列表里 claimStatus=CLAIMED 的历史窗口 —— 领取记录回填
    - trae: 只有当日状态,没有历史接口 —— 如实跳过
    """
    result: dict[str, Any] = {}
    conn = connect()
    try:
        result["workbuddy"] = _backfill_workbuddy(conn)
        result["qoder"] = _backfill_qoder(conn)
        result["trae"] = "skipped: 服务端没有历史接口,只有当日状态"
    finally:
        conn.close()
    return result


def _backfill_workbuddy(conn: sqlite3.Connection) -> Any:
    """workbuddy 活跃日历回填;凭据/网络失败返回原因字符串,不抛。"""
    try:
        net, failure = _workbuddy_net()
        if net is None:
            return "skipped: %s" % failure
        code, body = net.get("/v2/activity/growth/heatmap")
        if not 200 <= code < 300:
            return "skipped: 查活跃日历失败 HTTP %s" % code
        today, scores = _heatmap_scores(body)
    except Exception as exc:  # noqa: BLE001
        return "skipped: %s: %s" % (type(exc).__name__, exc)
    return _insert_rows(conn, _activity_rows(today, scores))


def _activity_rows(today: Any, scores: dict) -> list[dict[str, Any]]:
    """活跃日历 → 台账行(纯函数,便于测试);未来日期跳过。"""
    rows = []
    for day in sorted(scores):
        if day > today:
            continue
        score = scores[day]
        rows.append({
            "ts": "%s%s" % (day.isoformat(), _BACKFILL_TIME),
            "domain": "workbuddy",
            "action": "backfill",
            "result": "ACTIVE" if score > 0 else "NO_ACTIVITY",
            "needs_attention": False,
            "report": "历史活跃记录：score=%s（服务端日历回填）" % score,
            "detail": {"source": "server-backfill", "date": day.isoformat(), "score": score},
        })
    return rows


def _workbuddy_net() -> tuple[Any, str | None]:
    """构造 workbuddy 的请求器(与其 service 同一套凭据/解密/头)。"""
    from auto_signin_kit.core.config import load_domain_config_model
    from auto_signin_kit.core.httpclient import Budget
    from auto_signin_kit.domains.workbuddy import service as wb
    from auto_signin_kit.domains.workbuddy.net import Net
    from auto_signin_kit.domains.workbuddy.schema import WorkbuddyDomainConfig

    config = load_domain_config_model("workbuddy", WorkbuddyDomainConfig, merge_local=True)
    budget_value, _ = wb.parse_budget(config, poll=False)
    budget = Budget(budget_value)
    session, failure = wb.load_credentials()
    if failure is not None:
        return None, "本地凭据缺失"
    resolved = wb.resolve_session(session, budget)
    endpoint = ((resolved.get("auth") or {}).get("endpoint") or config.endpoint
                or wb.DEFAULT_ENDPOINT).rstrip("/")
    return Net(endpoint, wb.build_headers(resolved), budget), None


def _heatmap_scores(body: dict) -> tuple[Any, dict]:
    """借用 workbuddy 的日历解析(校验规则同源,不重写一份)。"""
    from auto_signin_kit.domains.workbuddy import rewards

    return rewards.heatmap_scores(body)


def _backfill_qoder(conn: sqlite3.Connection) -> Any:
    """qoder 活动领取记录回填;凭据/网络失败返回原因字符串,不抛。"""
    try:
        from auto_signin_kit.core.config import load_domain_config_model
        from auto_signin_kit.core.httpclient import Budget
        from auto_signin_kit.domains.qoder.api import build_api, parse_budget
        from auto_signin_kit.domains.qoder.schema import QoderDomainConfig

        config = load_domain_config_model("qoder", QoderDomainConfig, merge_local=True)
        budget_value, _ = parse_budget(config)
        api, _info, stale = build_api(config)
        if stale is not None:
            return "skipped: 登录态已过期，请重新登录 Qoder CN"
        code, payload, _err = api.campaigns(budget=Budget(budget_value))
        if not 200 <= code < 300:
            return "skipped: 查活动列表失败 HTTP %s" % code
    except Exception as exc:  # noqa: BLE001
        return "skipped: %s: %s" % (type(exc).__name__, exc)
    return _insert_rows(conn, _qoder_rows(payload))


def _qoder_rows(payload: Any) -> list[dict[str, Any]]:
    """活动列表 → 台账行: 只取 claimStatus=CLAIMED 的 CLAIM_BENEFIT 活动。"""
    from datetime import datetime

    rows = []
    campaigns = payload.get("campaigns") if isinstance(payload, dict) else None
    for campaign in campaigns if isinstance(campaigns, list) else []:
        if not isinstance(campaign, dict) or campaign.get("actionType") != "CLAIM_BENEFIT":
            continue
        if campaign.get("claimStatus") != "CLAIMED":
            continue
        end = campaign.get("endAt")
        if not isinstance(end, (int, float)):
            continue
        day = datetime.fromtimestamp(end, tz=UTC).astimezone().date().isoformat()
        benefit = campaign.get("benefit") or {}
        amount = benefit.get("amount")
        key = str(campaign.get("campaignKey") or campaign.get("campaignId") or "?")
        rows.append({
            "ts": "%s%s" % (day, _BACKFILL_TIME),
            "domain": "qoder",
            "action": "backfill",
            "result": "CLAIMED",
            "needs_attention": False,
            "report": "历史领取：活动 %s（%s Credits，窗口至 %s）"
                      % (key, amount if amount is not None else "?", day),
            "detail": {"source": "server-backfill", "campaign": key,
                       "credits": amount, "window_end": day},
        })
    return rows


def _insert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """批量插入服务端回填行(source=backfill,逐条去重);返回实际新增行数。

    行字典沿用事件载荷的字段名 ``ts``,落库时映射到 ``executed_at`` 列。
    """
    inserted = 0
    for record in rows:
        detail = record.pop("detail")
        record["detail"] = json.dumps(detail, ensure_ascii=False)
        if _already_exists(conn, record):
            continue
        with conn:
            conn.execute(
                "INSERT INTO ask_signin_runs (executed_at, domain, action, source,"
                " result, needs_attention, report, detail)"
                " VALUES (?, ?, ?, 'backfill', ?, ?, ?, ?)",
                (
                    record["ts"], record["domain"], record.get("action"),
                    record["result"], 1 if record.get("needs_attention") else 0,
                    record.get("report"), record["detail"],
                ),
            )
        inserted += 1
    return inserted
