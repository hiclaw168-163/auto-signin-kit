"""时间预算解析(多域共享): 环境变量优先,配置兜底,非法/越界不抛异常。

抽取自 workbuddy/trae/qoder 三域近似重复的 ``parse_budget``
(2026-11-03 四域共性分析结论 #3)。语义:

1. 环境变量 ``env_var`` 合法且为正 → 优先采用;
2. 环境变量非法/非正 → 显式回落 ``default`` 并给 warning(不静默吞);
3. 未配置环境变量时用 ``configured``,缺配置/非正回落 ``default``;
4. 超过 ``maximum`` → 夹到上限并给 warning(定时任务 ExecutionTimeLimit 余量)。
"""

from __future__ import annotations

import os


def parse_budget(
    configured: float | None,
    default: float,
    maximum: float,
    env_var: str,
) -> tuple[float, str | None]:
    """返回 ``(秒数, 告警或 None)``;绝不抛异常,非法输入都有回落值。"""
    raw = configured
    raw_str = os.environ.get(env_var)
    if raw_str:
        try:
            raw = float(raw_str)
        except (TypeError, ValueError):
            return default, (
                "%s=%r 不是数字，已回落 %s 秒" % (env_var, raw_str, int(default)))
        if raw <= 0:
            # "0" 是非空字符串,靠 not 兜不住;且 0 会让每个请求都直接放弃。
            return default, (
                "%s=%s 必须为正数，已回落 %s 秒" % (env_var, raw_str, int(default)))
    elif raw is None or raw <= 0:
        raw = default
    if raw > maximum:
        return maximum, (
            "%s=%s 超过 %d 秒上限（须小于定时任务的 ExecutionTimeLimit），已夹到上限"
            % (env_var, raw, int(maximum)))
    return raw, None
