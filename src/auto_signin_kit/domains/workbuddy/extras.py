"""成长中心 · 第三段: 盲盒抽奖 / 能量开 Buddy / 展示值 与本轮汇总。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from auto_signin_kit.core.logging import log_debug
from auto_signin_kit.domains.workbuddy.fields import (
    as_int,
    client_token,
    dig,
)
from auto_signin_kit.domains.workbuddy.growth import (
    GROWTH_BASE,
    GrowthState,
    StopRoundError,
    check_auth,
)
from auto_signin_kit.domains.workbuddy.net import Net


def _is_no_chance(msg: Any) -> bool:
    """抽奖失败是否只是"没有次数"——常态不是故障(400 + insufficient ... balance)。

    与真正的参数错误同为 400,只看状态码会把常态记成失败,轮询会每轮强制落盘。
    """
    text = str(msg or "").lower()
    if not text:
        return False
    if "insufficient" in text or "not enough" in text:
        return "chance" in text or "balance" in text
    return "no chance" in text


def run_lottery(net: Net, state: GrowthState, budget_left: float) -> None:
    """盲盒: 查机会 → 有次数就开一次;写路径不可逆,剩余次数留给下一轮更稳妥。"""
    if budget_left <= 0:
        state.note("时间预算耗尽，盲盒跳过")
        return
    try:
        lcode, lbody = net.get(GROWTH_BASE + "/lottery/chances")
        check_auth(lcode)
        skipped = state.note_http(lcode, lbody, "查抽奖机会")
        chances = 0 if skipped else as_int(dig(lbody, "balance"))
        if chances <= 0:
            return
        dcode, dbody = net.post(GROWTH_BASE + "/lottery/draw", {"client_token": client_token()})
        check_auth(dcode)
        if 200 <= dcode < 300:
            _note_prize(dbody, state, chances)
        else:
            msg = dig(dbody, "msg") or ""
            if _is_no_chance(msg):
                state.note("开盲盒：%s" % (msg or "无抽奖机会"))
            else:
                state.fail("开盲盒失败：%s" % (msg or "HTTP %s" % dcode), dcode)
    except StopRoundError:
        raise
    except Exception as exc:  # noqa: BLE001
        state.fail("盲盒模块异常（%s: %s）" % (type(exc).__name__, exc), -1)


def _note_prize(dbody: dict, state: GrowthState, chances: int) -> None:
    prize = dig(dbody, "prize_name") or dig(dbody, "prize") or "未知"
    if not isinstance(prize, str):
        # prize 可能是对象/数字;不转字符串会把一次已中的奖变成"模块异常"。
        prize = str(prize)
    # 实物奖要用户自己填收件信息;脚本绝不代填,但必须提醒,否则奖品卡在未填地址状态。
    if dig(dbody, "need_address") or dig(dbody, "require_address"):
        prize += "（实物奖，需到成长中心填写收件信息）"
    state.ok("开盲盒获得：%s" % prize)
    if chances > 1:
        state.note("还剩 %s 次抽奖机会，下轮继续" % (chances - 1))


def run_buddy_box(net: Net, state: GrowthState, budget_left: float) -> None:
    """能量盲盒: affordable 达到开销就开,数量 min(affordable, max_open_count)。"""
    if budget_left <= 0:
        state.note("时间预算耗尽，Buddy 盲盒跳过")
        return
    try:
        qcode, qbody = net.get(GROWTH_BASE + "/buddy/quota")
        check_auth(qcode)
        if state.note_http(qcode, qbody, "查 Buddy 能量"):
            return
        affordable = as_int(dig(qbody, "affordable"))
        max_open = as_int(dig(qbody, "max_open_count"), 1) or 1
        if affordable <= 0:
            return
        count = min(affordable, max_open)
        ocode, obody = net.post(
            GROWTH_BASE + "/buddy/open",
            {"count": count, "client_token": client_token()},
        )
        check_auth(ocode)
        if 200 <= ocode < 300:
            name = dig(obody, "buddy") or dig(obody, "name") or dig(obody, "buddies")
            if not isinstance(name, str):
                name = "新 Buddy"
            state.ok("开 Buddy 盲盒 ×%s（%s）" % (count, name))
        else:
            msg = dig(obody, "msg") or ""
            state.fail("开 Buddy 盲盒失败：%s" % (msg or "HTTP %s" % ocode), ocode)
    except StopRoundError:
        raise
    except Exception as exc:  # noqa: BLE001
        state.fail("Buddy 盲盒模块异常（%s: %s）" % (type(exc).__name__, exc), -1)


def run_display(
    net: Net, state: GrowthState, streak_body: dict | None, budget_left: float
) -> tuple[Any, Any]:
    """展示性取值(能量余额/连签天数): 预算不够或失败都不计,绝不让展示信息炸整轮。"""
    energy = None
    streak_days = None
    if budget_left > 0:
        try:
            ecode, ebody = net.get(GROWTH_BASE + "/energy")
            if ecode not in (401, 403) and 200 <= ecode < 300:
                energy = dig(ebody, "balance")
        except Exception as exc:  # noqa: BLE001 — 展示值失败只降级不炸整轮,但留痕到调试日志
            log_debug("能量余额获取失败(展示用,已跳过): %s: %s" % (type(exc).__name__, exc))
    # 本轮没取过 /streak 或补登改了天数时才重新取;同一轮不打两次同一接口。
    try:
        if streak_body is not None and not state.stale:
            streak_obj = dig(streak_body, "streak") or {}
            streak_days = streak_obj.get("days") if isinstance(streak_obj, dict) else None
        elif budget_left > 0:
            scode2, sbody2 = net.get(GROWTH_BASE + "/streak")
            if scode2 not in (401, 403):
                streak_obj = dig(sbody2, "streak") or {}
                streak_days = streak_obj.get("days") if isinstance(streak_obj, dict) else None
    except Exception as exc:  # noqa: BLE001 — 同上,展示值降级但留痕
        log_debug("连签天数获取失败(展示用,已跳过): %s: %s" % (type(exc).__name__, exc))
    return energy, streak_days


def run_growth(
    net: Net, budget_left_fn: Callable[[], float], taskbot: bool = False,
) -> tuple[int, dict[str, Any]]:
    """成长中心总入口: 七段顺序执行 → 汇总为 ``GROWTH`` 结果与退出码。

    步骤: 旅行(growth.run_travel) → 任务(run_tasks) → 进度制造(taskbot,
    开关默认关) → 补登(rewards.run_makeup) → 兑换(run_redeem) → 抽奖
    → 能量盲盒 → 展示值。一段失败不拖垮其余。
    """
    state = GrowthState()
    streak_body = None
    energy = None
    streak_days = None
    try:
        from auto_signin_kit.domains.workbuddy.growth import run_tasks, run_travel
        from auto_signin_kit.domains.workbuddy.rewards import run_makeup, run_redeem

        run_travel(net, state, budget_left_fn())
        tasks = run_tasks(net, state, budget_left_fn())
        if taskbot and tasks:
            from auto_signin_kit.domains.workbuddy.taskbot import run_progress

            run_progress(net, state, tasks, budget_left_fn)
        streak_body = run_makeup(net, state, budget_left_fn())
        run_redeem(net, state, budget_left_fn())
        run_lottery(net, state, budget_left_fn())
        run_buddy_box(net, state, budget_left_fn())
        energy, streak_days = run_display(net, state, streak_body, budget_left_fn())
    except StopRoundError as stop:
        return 1, stop.dto
    tail = []
    if energy is not None:
        tail.append("能量 %s" % energy)
    if streak_days is not None:
        tail.append("连签 %s 天" % streak_days)
    if state.credits_gained:
        tail.append("本次 +共 %s 积分" % state.credits_gained)
    if state.parts:
        report = "；".join(state.parts)
    elif state.failures:
        report = "成长中心各步骤均失败"
    else:
        report = "成长中心无可领取项"
    if tail:
        report += "（%s）" % "，".join(tail)
    out: dict[str, Any] = {
        "result": "GROWTH",
        "report": report,
        "credits_gained": state.credits_gained,
        "energy": energy,
        "streak_days": streak_days,
        "idle": state.idle,
    }
    if state.failures:
        out["failures"] = state.failures
    return 1 if state.hard_failures else 0, out
