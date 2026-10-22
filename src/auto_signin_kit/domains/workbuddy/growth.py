"""成长中心 · 第一段: Buddy 旅行(领礼物 + 派出发) 与 任务系统(接单 + 领奖)。

每个子步骤独立 try,一段失败不影响其余领取;认证/权限拒绝时结束本轮
统一走 ``StopRound`` 异常向上传递。显式顺序理由: 任务奖放在抽奖前——任务送的
抽奖机会/能量,后面的步骤马上能用上。
"""

from __future__ import annotations

from typing import Any

from auto_signin_kit.core.httpclient import BUDGET_OUT, NO_NETWORK
from auto_signin_kit.domains.workbuddy.checkin import auth_failure_dto
from auto_signin_kit.domains.workbuddy.fields import (
    as_int,
    dig,
    first_int,
    fmt_credit,
    fmt_eta,
    is_hard_failure,
)
from auto_signin_kit.domains.workbuddy.net import Net

GROWTH_BASE = "/v2/activity/growth"


class StopRoundError(Exception):
    """认证/权限被拒: 停止用同一凭据继续请求,把结果上抛给本轮汇总。"""

    def __init__(self, dto: dict[str, Any]) -> None:
        """结果 dto 保存在异常对象上,由汇总层按需检查。"""
        super().__init__(str(dto.get("reason") or dto.get("result")))
        self.dto = dto


class GrowthState:
    """一轮成长中心的累计状态: 汇报分项/积分/成败计数/是否空跑。"""

    def __init__(self) -> None:
        """空态起步。"""
        self.parts: list[str] = []
        self.credits_gained = 0
        self.failures = 0
        self.hard_failures = 0
        self.successes = 0
        self.stale = False  # 补登会改变连签天数,此时需重新取状态

    def ok(self, text: str, credits: int = 0) -> None:
        """记一笔成功(词进汇总,积分进累计)。"""
        self.successes += 1
        self.credits_gained += credits
        self.parts.append(text)

    def fail(self, text: str, code: int) -> None:
        """记一笔失败(硬失败单独计数,用于退出码判定)。"""
        self.failures += 1
        self.hard_failures += int(is_hard_failure(code))
        self.parts.append(text)

    def note(self, text: str) -> None:
        """不改变成败计数的说明(名额用完/预算耗尽等)。"""
        self.parts.append(text)

    def note_http(self, code: int, body: Any, label: str, required: bool = False) -> bool:
        """前置查询非 2xx 的统一记录;返回 True 表示调用方应跳过后续处理。

        硬失败(5xx/网络/预算)必须计入,否则 successes=0 且 failures=0 会被判
        成空跑,轮询既不写日志又返回 0,服务端故障彻底无声无息。
        """
        if 200 <= code < 300 and (not required or _api_ok(code, body)):
            return False
        reason = _http_label(code)
        detail = str(body.get("error") or body.get("msg") or "") if isinstance(body, dict) else ""
        if detail and detail != reason:
            self.fail("%s失败：%s（%s）" % (label, reason, detail), code)
        else:
            self.fail("%s失败：%s" % (label, detail or reason), code)
        return True

    @property
    def idle(self) -> bool:
        """空跑: 既没领到东西也没出错(旅行在路上/名额用完/无可领取项)。"""
        return self.successes == 0 and self.failures == 0


def _api_ok(code: int, body: Any) -> bool:
    business = body.get("code") if isinstance(body, dict) else None
    return business is None or (type(business) is int and business == 0)


def _http_label(code: int) -> str:
    if code == NO_NETWORK:
        return "网络不可达"
    if code == BUDGET_OUT:
        return "时间预算耗尽"
    return "HTTP %s" % code


def check_auth(code: int) -> None:
    """非 401/403 直接通过;命中则抛 StopRoundError 结束本轮。"""
    if code in (401, 403):
        raise StopRoundError(auth_failure_dto(code))


def run_travel(net: Net, state: GrowthState, budget_left: float) -> None:
    """Buddy 旅行: arrived 领礼物 → idle 且有名额才派出发 → traveling 报 ETA。"""
    try:
        scode, sbody = net.get(GROWTH_BASE + "/buddy/travel/status")
        if scode == NO_NETWORK:
            state.fail("网络不可达，成长中心跳过（%s）" % ((sbody or {}).get("error") or ""), scode)
            return
        if scode == BUDGET_OUT:
            state.fail("时间预算耗尽，成长中心跳过", scode)
            return
        check_auth(scode)
        travel = dig(sbody, "state") if 200 <= scode < 300 else None
        daily_limit = bool(
            dig(sbody, "daily_limit_reached")
        ) if 200 <= scode < 300 else False
        state.note_http(scode, sbody, "查旅行状态")
        if travel == "arrived":
            claimed = _claim_gift(net, state, sbody)
            if claimed:
                # 只有领取成功才视为 idle(允许派出发);失败则不动,避免盖掉未领取奖励。
                travel = "idle"
        if travel == "idle" and daily_limit:
            # 名额已用完直接收手,不碰 config/depart,省两个请求和一条必然的失败。
            state.note("今日旅行名额已用完")
        elif travel == "idle":
            _depart(net, state, budget_left)
        elif travel == "traveling":
            loc_name = (dig(sbody, "location") or {}).get("name", "?")
            state.note(
                "Buddy 旅行中（%s%s）" % (
                    loc_name,
                    fmt_eta(dig(sbody, "arrive_at"), dig(sbody, "server_now")),
                )
            )
    except StopRoundError:
        raise
    except Exception as exc:  # noqa: BLE001  # 单步失败不拖垮其余领取
        state.fail("旅行模块异常（%s: %s）" % (type(exc).__name__, exc), -1)


def _claim_gift(net: Net, state: GrowthState, sbody: Any) -> bool:
    """领到达的礼物;只有领取成功才返回 True(允许随后的派出发)。"""
    payload = {"record_id": dig(sbody, "record_id")}
    ccode, cbody = net.post(GROWTH_BASE + "/buddy/travel/claim", payload)
    check_auth(ccode)
    if 200 <= ccode < 300 and dig(cbody, "reward_credit") is not None:
        got = as_int(dig(cbody, "reward_credit"))
        state.ok("领旅行礼物 +%s 积分" % fmt_credit(got), got)
        return True
    msg = (dig(cbody, "msg") or "") if isinstance(cbody, dict) else ""
    state.fail("领旅行礼物失败：%s" % (msg or "HTTP %s" % ccode), ccode)
    return False


def _depart(net: Net, state: GrowthState, budget_left: float) -> None:
    if budget_left <= 0:
        state.note("时间预算耗尽，派 Buddy 跳过")
        return
    ccode, cbody = net.get(GROWTH_BASE + "/buddy/travel/config")
    check_auth(ccode)
    locs = dig(cbody, "locations") if 200 <= ccode < 300 else None
    if not (locs and isinstance(locs[0], dict)):
        state.note_http(ccode, cbody, "查旅行配置")
        return
    loc = locs[0]
    dcode, dbody = net.post(GROWTH_BASE + "/buddy/travel/depart", {"location_id": loc.get("id")})
    check_auth(dcode)
    if 200 <= dcode < 300:
        loc_name = (dig(dbody, "location") or {}).get("name", "?")
        dur = dig(dbody, "duration_hours")
        dur = dur or (dig(dbody, "location") or {}).get("duration_hours", "?")
        state.ok("派 Buddy 去%s（%s 小时后回）" % (loc_name, dur))
    else:
        msg = dig(dbody, "msg") or ""
        state.fail("派 Buddy 失败：%s" % (msg or "HTTP %s" % dcode), dcode)


ACCEPT_BATCH_SIZE = 20


def run_tasks(net: Net, state: GrowthState, budget_left: float) -> list | None:
    """任务系统: 批量接单(not_accepted → 开始计进度) + 领取 completed 的任务奖。

    返回拉到的任务列表(进度制造器 taskbot 复用,免去重复请求);
    列表查询失败时返回 None。
    """
    if budget_left <= 0:
        state.note("时间预算耗尽，任务领奖跳过")
        return None
    try:
        tcode, tbody = net.get(GROWTH_BASE + "/tasks")
        check_auth(tcode)
        if state.note_http(tcode, tbody, "查任务列表"):
            return None
        tasks = dig(tbody, "tasks") or []
        titles = {
            t.get("task_code"): t.get("title", t.get("task_code"))
            for t in tasks
            if isinstance(t, dict)
        }
        pending = [
            t.get("task_code")
            for t in tasks
            if isinstance(t, dict) and t.get("task_code") and not t.get("locked")
            and t.get("accept_status") == "not_accepted"
        ]
        for i in range(0, len(pending), ACCEPT_BATCH_SIZE):
            if budget_left <= 0:
                state.note("时间预算耗尽，剩余任务下次再接单")
                break
            _accept_batch(net, state, titles, pending[i : i + ACCEPT_BATCH_SIZE])
        for task in tasks:
            if budget_left <= 0:
                state.note("时间预算耗尽，剩余任务奖下次再领")
                break
            _claim_task_reward(net, state, titles, task)
        return tasks if isinstance(tasks, list) else None
    except StopRoundError:
        raise
    except Exception as exc:  # noqa: BLE001
        state.fail("任务模块异常（%s: %s）" % (type(exc).__name__, exc), -1)
        return None


def _accept_batch(net: Net, state: GrowthState, titles: dict, batch: list) -> None:
    acode, abody = net.post(GROWTH_BASE + "/tasks/accept", {"task_codes": batch})
    check_auth(acode)
    results = dig(abody, "results")
    if not isinstance(results, list):
        fallback_status = "ok" if 200 <= acode < 300 else "error"
        results = [
            {"task_code": c, "status": fallback_status, "message": dig(abody, "msg")}
            for c in batch
        ]
    for r in results:
        title = titles.get(r.get("task_code"), r.get("task_code"))
        if r.get("status") == "error":
            # 接单失败必须报出来(常见 prerequisite not met),静默吞掉等于接口坏了没人知道。
            msg = r.get("message") or "HTTP %s" % acode
            state.fail("领取任务「%s」失败：%s" % (title, msg), acode)
        else:
            state.ok("领取任务「%s」（进度开始计）" % title)


def _claim_task_reward(net: Net, state: GrowthState, titles: dict, task: dict) -> None:
    if task.get("locked") or task.get("accept_status") != "completed":
        return  # 只有 completed 才发奖,且走独立路径。
    code = task.get("task_code")
    title = titles.get(code, code)
    try:
        ccode, cbody = net.post(GROWTH_BASE + "/tasks/%s/claim" % code, {})
        check_auth(ccode)
        if 200 <= ccode < 300 and not dig(cbody, "already_claimed"):
            rc = first_int(cbody, "credit", task.get("reward_credit"))
            re_ = first_int(cbody, "energy", task.get("reward_energy"))
            state.ok("领任务奖「%s」+credit%s+energy%s" % (title, rc, re_), rc)
        elif 200 <= ccode < 300:
            state.note("任务奖「%s」已领过" % title)
        else:
            msg = dig(cbody, "msg") or ""
            state.fail("领任务奖「%s」失败：%s" % (title, msg or "HTTP %s" % ccode), ccode)
    except StopRoundError:
        raise
    except Exception as exc:  # noqa: BLE001
        state.fail("领任务奖「%s」异常（%s: %s）" % (code, type(exc).__name__, exc), -1)
