"""响应字段与数值的小工具:信封结构兼容、安全取数、人话格式化。"""

from __future__ import annotations

import math
import uuid
from typing import Any

from auto_signin_kit.core.httpclient import BUDGET_OUT, NO_NETWORK


def dig(obj: Any, key: str) -> Any:
    """在可能被 data/result 包裹的响应里找字段,兼容信封结构。"""
    if isinstance(obj, dict):
        if key in obj and obj[key] is not None:
            return obj[key]
        for wrapper in ("data", "result", "resp", "response"):
            if wrapper in obj and isinstance(obj[wrapper], dict):
                found = dig(obj[wrapper], key)
                if found is not None:
                    return found
    return None


def fmt_credit(v: Any) -> Any:
    """积分显示用:能转 int 就转,否则原样返回。"""
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return v


def as_int(v: Any, default: int = 0) -> int:
    """把可能为字符串的数字安全地转 int;OverflowError 必须一并捕获。

    json.loads 默认接受 Infinity,服务端返回这个字面量时 int(inf) 抛的是
    OverflowError 而非 ValueError,漏掉会把整段解析炸掉。
    """
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return int(float(v))  # 兼容 "1.5"/"1e3" 这类数字串
    except (TypeError, ValueError, OverflowError):
        return default


def first_int(body: dict, key: str, fallback: int = 0) -> int:
    """优先取响应里的实际数值,挖不到才用回落值(响应值优先于活动配置)。"""
    if isinstance(body, dict):
        value = dig(body, key)
        if value is not None:
            return as_int(value)
    return as_int(fallback)


def fmt_eta(arrive_at: Any, server_now: Any) -> str:
    """把服务端返回的 Unix 时间戳换算成"还有多久回来";纯展示,绝不抛异常。

    json.loads 接受 Infinity/NaN,非有限值同样按缺失处理。
    """
    try:
        left = float(arrive_at) - float(server_now)
    except (TypeError, ValueError, OverflowError):
        return ""
    if not math.isfinite(left):
        return ""
    if left <= 0:
        return "，已到达待领取"
    minutes = int(round(left / 60.0))
    if minutes < 60:
        return "，约 %d 分钟后回" % max(1, minutes)
    return "，约 %.1f 小时后回" % (left / 3600.0)


def client_token(prefix: str = "u") -> str:
    """活动接口(抽奖/连登兑换)要求的防重放 token;缺了 /lottery/draw 直接 400。"""
    return "%s-%s" % (prefix, uuid.uuid4())


def api_success(code: int, body: Any) -> bool:
    """补登相关接口同时检查 HTTP 状态与 JSON 业务码。"""
    business_code = body.get("code") if isinstance(body, dict) else None
    return 200 <= code < 300 and (
        business_code is None or (type(business_code) is int and business_code == 0)
    )


def is_hard_failure(code: int) -> bool:
    """5xx 与"没拿到响应"算硬失败;4xx 是业务规则(每日上限/活动结束等)不算。"""
    return code >= 500 or code in (NO_NETWORK, BUDGET_OUT)


def http_label(code: int) -> str:
    """把伪 HTTP 码翻译成人话;-1/-2 一类是"没拿到响应"的标记。"""
    if code == NO_NETWORK:
        return "网络不可达"
    if code == BUDGET_OUT:
        return "时间预算耗尽"
    return "HTTP %s" % code
