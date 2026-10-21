"""workbuddy 一轮日常的流水线编排: 签到(含领取) → 成长中心 → 聚合回报。

用 orchestration.Pipeline 落地多步骤契约: 签到被网络/认证门控中止时,后续步骤
自动跳过;成长中心的业务失败不吞掉签到已成功的事实,由聚合函数合并两边结果后
统一回报。步骤都有远程副作用(已推送服务端),不可撤销,回滚一律为空操作——
断签场景靠轮询幂等补签。
"""

from __future__ import annotations

from typing import Any

from auto_signin_kit.domains.workbuddy import checkin
from auto_signin_kit.domains.workbuddy.extras import run_growth
from auto_signin_kit.domains.workbuddy.net import Net
from auto_signin_kit.domains.workbuddy.session import AuthError
from auto_signin_kit.orchestration.pipeline import Pipeline, PipelineError
from auto_signin_kit.orchestration.step import StepResult

#: 这些签到结果意味着后续步骤不可能成功,流水线在此中止,成长中心跳过。
GROWTH_SKIP_REASONS = {
    "NETWORK": "网络不可达或时间预算耗尽，成长中心跳过",
    "TIMEOUT": "网络不可达或时间预算耗尽，成长中心跳过",
    "NO_SESSION": "认证或权限检查未通过，成长中心跳过",
    "AUTH_REJECTED": "认证或权限检查未通过，成长中心跳过",
    "FORBIDDEN": "认证或权限检查未通过，成长中心跳过",
}


class CheckinStep:
    """签到步: 查状态 → 未签才领(checkin.run_auto);门控失败中止流水线。"""

    name = "签到"

    def __init__(self, net: Net) -> None:
        """net 由调用方注入,签到步不自行建立连接。"""
        self.net = net

    def run(self, ctx: dict[str, Any]) -> StepResult:
        """执行签到并把结果写入 ctx;门控失败返回 ok=False 中止流水线。"""
        code, out = checkin.run_auto(self.net)
        ctx["checkin_code"] = code
        ctx["checkin_out"] = out
        skip = GROWTH_SKIP_REASONS.get(str(out.get("result")))
        if skip is None:
            return StepResult(ok=True)
        ctx["growth"] = skip
        ctx["growth_result"] = out.get("result")
        return StepResult(ok=False, message=str(out.get("report") or out.get("result")))

    def rollback(self, ctx: dict[str, Any]) -> None:
        """签到已推送服务端,不可撤销;断签场景靠下次轮询幂等补签。"""


class GrowthStep:
    """成长中心步: 业务失败不中止回报——结果进 ctx,由聚合函数裁决退出码。"""

    name = "成长中心"

    def __init__(self, net: Net) -> None:
        """net 由调用方注入,预算余量在执行时实时读取。"""
        self.net = net

    def run(self, ctx: dict[str, Any]) -> StepResult:
        """执行成长中心任务并把结果写入 ctx;业务失败折算进结果,不中止流水线。"""
        try:
            gcode, gout = run_growth(self.net, self.net.budget_remaining)
        except AuthError as e:
            gcode, gout = 1, e.output()
        except Exception as e:  # noqa: BLE001 — 单步异常折算成 ERROR 结果,不让一轮日常崩掉
            gcode, gout = 1, {"result": "ERROR", "needs_attention": True,
                              "report": "成长中心异常（%s: %s）" % (type(e).__name__, e)}
        ctx["growth_code"] = gcode
        ctx["growth_out"] = gout
        ctx["growth"] = gout.get("report")
        ctx["growth_result"] = gout.get("result")
        return StepResult(ok=True)

    def rollback(self, ctx: dict[str, Any]) -> None:
        """成长任务的领取已推送服务端,不可撤销。"""


def _aggregate_daily(ctx: dict[str, Any]) -> tuple[int, dict, bool]:
    """合并签到与成长中心的结果: 成长失败抬高退出码,但不吞签到已成功的事实。"""
    code = ctx["checkin_code"]
    out = ctx["checkin_out"]
    gcode = ctx.get("growth_code", 0)
    gout = ctx.get("growth_out")
    out["growth"] = ctx.get("growth")
    out["growth_result"] = ctx.get("growth_result")
    if gout is not None and (gout.get("credits_gained") or gcode != 0):
        out["report"] += "；" + gout["report"]
    if gcode != 0 and code == 0:
        code = gcode
    out["needs_attention"] = code != 0
    quiet = out.get("result") in ("ALREADY", "INACTIVE") and bool(gout and gout.get("idle"))
    return code, out, quiet


def run_daily(net: Net) -> tuple[int, dict, bool]:
    """一轮完整日常: 签到 → 成长中心;返回 (退出码, 结果, 是否空跑)。

    轮询也走它而不是只跑成长中心: 签到原本一天一次,撞上关机/断网当天断签;
    轮询先查状态、未签才补(幂等),等于一天七次机会。成长中心的问题不吞掉
    签到已成功的事实,但会让本轮退出码非零、needs_attention 置位。
    """
    ctx: dict[str, Any] = {}
    try:
        Pipeline([CheckinStep(net), GrowthStep(net)]).run(ctx)
    except PipelineError:
        if "checkin_out" not in ctx:
            raise  # 签到步没产出结果(意外异常),原样上抛,不做静默兜底
        # 门控中止(网络/认证): 签到结果已在 ctx,照常聚合汇报
    return _aggregate_daily(ctx)
