"""台账落库: emit_result 脱敏事件的按域分流写入。

写入是 **best-effort**: 台账永远不能影响签到本身,任何数据库错误都在本层
静默吞掉。落库的内容是 emit_result 脱敏后的 JSON 行——令牌在进库前就已
替换为 ``[REDACTED]``,台账里不会有敏感值。
"""

from __future__ import annotations

import json
import sqlite3

from auto_signin_kit.core.report import normalize_result
from auto_signin_kit.core.store.connection import connect
from auto_signin_kit.core.store_migrations import _split_record, local_naive

#: 调度域的结果不进签到台账,单独记审计表。
SCHEDULE_DOMAIN = "schedule"


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
                    _insert_schedule_change(conn, record)
                else:
                    _insert_signin_run(conn, record, payload)
        finally:
            conn.close()
        return True
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return False


def _insert_schedule_change(conn: sqlite3.Connection, record: dict) -> None:
    """调度变更审计行: 注册/注销动作与涉及的任务时间。

    统一审计字段: 审计行只插不改,created_at/updated_at 与 executed_at 同值。
    """
    executed_at = local_naive(record["ts"])
    conn.execute(
        "INSERT INTO ask_schedule_changes (executed_at, action, platform,"
        " tasks, report, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            executed_at,
            str(record.get("action") or "unknown"),
            record.get("platform"),
            json.dumps(
                {"done": record.get("done"), "failed": record.get("failed")},
                ensure_ascii=False,
            ),
            record.get("report"),
            executed_at,
            executed_at,
        ),
    )


def _insert_signin_run(conn: sqlite3.Connection, record: dict, payload: str) -> None:
    """签到运行台账行: 词表归一 + 请求/响应拆列,detail 保留整行兼容。"""
    request, response = _split_record(record)
    domain = str(record.get("domain") or "unknown")
    # 账户归属: 域回传 account 则用之,否则落每域默认户 'default';
    # 首次出现的 (domain, account) 自动进账户维表(积分业务的主体维度)
    account = str(record.get("account") or "default")
    executed_at = local_naive(record["ts"])
    conn.execute(
        "INSERT OR IGNORE INTO ask_accounts (domain, name, created_at, updated_at)"
        " VALUES (?, ?, ?, ?)",
        (domain, account, executed_at, executed_at))
    conn.execute(
        "INSERT INTO ask_signin_runs (executed_at, domain, action, source,"
        " result, needs_attention, report, request, response, detail, account,"
        " created_at, updated_at)"
        " VALUES (?, ?, ?, 'run', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            executed_at,
            domain,
            record.get("action"),
            # 归一化到标准词表: 域内部细粒度码(大小写/别名)进库
            # 前收口成 10 词,查询与聚合才有统一口径
            normalize_result(record.get("result")),
            1 if record.get("needs_attention") else 0,
            record.get("report"),
            request,
            response,
            payload,
            account,
            # 统一审计字段: 与 executed_at 同值(台账行只插不改)
            executed_at,
            executed_at,
        ),
    )
