"""run_action 命令分发骨架的公共片段(多域共享)。

抽取自五域 service.run_action 的逐字重复(2026-11-03 四域共性分析
结论 #2): 未知命令文案与"调度兜底触发时当天已成功则跳过"的判定。
各域的 emit 形状(dict vs make_result)与凭证/业务分支不在这里管。
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from auto_signin_kit.core.logging import log_debug
from auto_signin_kit.core.store import done_today

#: 缺省账户: 单账户配置的等价户名(与 core.config.schema 对齐)。
DEFAULT_ACCOUNT = "default"


def unknown_action_report(action: str, known: Sequence[str]) -> str:
    """未知命令的标准报告文案(全库统一口径)。"""
    return "未知命令：%s（可用：%s）" % (action, " / ".join(known))


def skip_done_today(
    domain: str,
    action: str,
    *,
    silent: bool,
    skip_when_done: Collection[str] = ("claim",),
    noun: str = "签到",
    account: str = DEFAULT_ACCOUNT,
) -> bool:
    """调度兜底触发(高密度时间点)时,当天该域该账户已成功 → 跳过执行与落库。

    返回 True 表示调用方应直接 ``return 0``。只有调度静默触发才跳过;
    用户手动执行永远真跑(哪怕当天已成功,也是明确意图)。
    账户维度(SPEC-12): done_today 按 (domain, account) 判,两户互不误跳。
    """
    if silent and action in skip_when_done and done_today(domain, account):
        log_debug("%s/%s: 今日已成功%s,调度触发跳过(不执行、不落库)"
                  % (domain, account, noun))
        return True
    return False
