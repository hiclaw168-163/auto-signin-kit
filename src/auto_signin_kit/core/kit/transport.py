"""传输层错误→结果 DTO 统一映射(多域共享)。

抽取自 trae/qoder 的传输层 if 链(2026-11-03 四域共性分析结论 #5)。
401/403 的 result 标签与字段形状全库一致,report 文案各域不同,经
``auth_reports`` 传入;NO_NETWORK/BUDGET_OUT 文案全库统一。
业务码(如 1001、710012*)不在这里处理,返回 None 交回业务层。
"""

from __future__ import annotations

import json
from typing import Any

from auto_signin_kit.core.httpclient import BUDGET_OUT, NO_NETWORK

#: 传输层伪状态之外需要映射的 HTTP 状态。
AUTH_HTTP_CODES = (401, 403)


def short_text(value: Any, limit: int = 200) -> str:
    """调试展示: dict 压成单行 JSON、字符串压白,超长截断带省略号。"""
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def transport_error_dto(
    code: int,
    payload: Any,
    err: str | None,
    *,
    auth_reports: dict[int, str] | None = None,
) -> dict | None:
    """传输层统一映射;返回 None 表示不归传输层管(业务码继续往下走)。

    ``auth_reports``: ``{401: "…", 403: "…"}`` 域专属指引文案;缺省给
    通用文案。其余字段形状(result/needs_attention/http/report)固定。
    """
    if code in AUTH_HTTP_CODES:
        if code == 401:
            report = (auth_reports or {}).get(401) or (
                "登录态被服务端拒绝（HTTP 401），请打开桌面端重新登录")
            return {"result": "AUTH_REJECTED", "needs_attention": True,
                    "http": code, "report": report}
        report = (auth_reports or {}).get(403) or (
            "被服务端拒绝（HTTP 403），请确认账号资格后重试")
        return {"result": "FORBIDDEN", "needs_attention": True,
                "http": code, "report": report}
    if code == NO_NETWORK:
        return {"result": "NETWORK", "needs_attention": True,
                "report": "网络请求失败：%s" % short_text(err)}
    if code == BUDGET_OUT:
        return {"result": "TIMEOUT", "needs_attention": True,
                "report": ((payload if isinstance(payload, dict) else {}) or {}).get(
                    "error") or "已达本次运行时间预算"}
    return None
