"""令牌/会话过期判定(多域共享,字段名参数化)。

抽取自 trae/qoder 两域逐字重复的 ``_parse_iso``/``token_expired``
(2026-11-03 四域共性分析结论 #6)。ISO8601 解析口径: 无时区视为 UTC。
"""

from __future__ import annotations

from datetime import UTC, datetime


def parse_iso(value: object) -> datetime | None:
    """解析 ISO8601 时间串;无时区视为 UTC;为空/非法返回 None。"""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def token_expired(info: dict, field: str = "expiredAt") -> bool:
    """``info[field]`` 已过期(在过去)返回 True;缺字段视为未过期。"""
    expires = parse_iso(info.get(field))
    return expires is not None and expires <= datetime.now(UTC)
