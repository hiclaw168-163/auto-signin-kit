"""growth/rewards 编排逻辑的离线测试: 状态聚合、任务解析、补登卡与连登兑换。"""

from datetime import date

import pytest

from auto_signin_kit.core.httpclient import NO_NETWORK
from auto_signin_kit.domains.workbuddy import growth, rewards


class _ScriptedNet:
    """按脚本依次应答 get/post 并记录调用;run_* 编排函数的离线替身。"""

    def __init__(self, gets=(), posts=()):
        self._gets = list(gets)
        self._posts = list(posts)
        self.calls = []

    def budget_remaining(self):
        return 300.0

    def get(self, suffix):
        self.calls.append(("GET", suffix, None))
        return self._gets.pop(0)

    def post(self, suffix, payload=None, retry=False):
        self.calls.append(("POST", suffix, payload))
        return self._posts.pop(0)


def test_growth_state_counters_and_idle():
    """ok/fail 计数决定 idle;5xx 与拿不到响应算硬失败,4xx 只算普通失败。"""
    state = growth.GrowthState()
    assert state.idle
    state.ok("领礼物 +3 积分", 3)
    state.fail("领取失败：HTTP 400", 400)
    assert not state.idle
    assert state.successes == 1 and state.credits_gained == 3
    assert state.failures == 1 and state.hard_failures == 0
    state.fail("网络不可达", NO_NETWORK)
    assert state.hard_failures == 1
    state.note("今日旅行名额已用完")
    assert len(state.parts) == 4 and state.failures == 2


def test_note_http_records_failures_and_signals_skip():
    """2xx 放行;非 2xx 与业务码异常都记失败并让调用方跳过,硬失败单独计数。"""
    state = growth.GrowthState()
    assert state.note_http(200, {"tasks": []}, "查任务列表") is False
    assert state.note_http(200, {"code": 0}, "查任务列表", required=True) is False
    assert state.note_http(NO_NETWORK, {"error": "连不上"}, "查任务列表") is True
    assert state.parts[-1] == "查任务列表失败：网络不可达（连不上）"
    assert state.hard_failures == 1
    assert state.note_http(200, {"code": 1, "msg": "业务异常"},
                           "查连登状态", required=True) is True
    assert state.parts[-1] == "查连登状态失败：HTTP 200（业务异常）"
    assert state.hard_failures == 1  # 业务码异常不算硬失败


def test_check_auth_raises_stop_round_error_with_dto():
    """401/403 必须抛 StopRoundError 并携带统一 dto;其他状态码直接放行。"""
    assert growth.check_auth(200) is None
    with pytest.raises(growth.StopRoundError) as denied:
        growth.check_auth(401)
    assert denied.value.dto["result"] == "AUTH_REJECTED"
    with pytest.raises(growth.StopRoundError) as forbidden:
        growth.check_auth(403)
    assert forbidden.value.dto["result"] == "FORBIDDEN"


def _tasks_body():
    """任务列表样本: 待接/锁定/可领奖/进行中/无标题待接五种形态。"""
    return {"tasks": [
        {"task_code": "T1", "title": "任务一", "accept_status": "not_accepted"},
        {"task_code": "T2", "title": "任务二", "locked": True,
         "accept_status": "not_accepted"},
        {"task_code": "T3", "accept_status": "completed",
         "reward_credit": 10, "reward_energy": 2},
        {"task_code": "T4", "accept_status": "accepted"},
        {"task_code": "T5", "accept_status": "not_accepted"},
    ]}


def test_run_tasks_accepts_claims_and_skips():
    """接单只挑未锁的 not_accepted;completed 才领奖;接单失败必须记失败不静默。"""
    net = _ScriptedNet(
        gets=[(200, _tasks_body())],
        posts=[
            (200, {"results": [
                {"task_code": "T1", "status": "ok"},
                {"task_code": "T5", "status": "error",
                 "message": "prerequisite not met"},
            ]}),
            (200, {"credit": 8}),
        ],
    )
    state = growth.GrowthState()
    growth.run_tasks(net, state, 10.0)
    accepts = [p for m, s, p in net.calls if s.endswith("/tasks/accept")]
    claims = [s for m, s, _ in net.calls if s.endswith("/claim")]
    assert accepts == [{"task_codes": ["T1", "T5"]}]
    assert claims == [growth.GROWTH_BASE + "/tasks/T3/claim"]
    assert state.successes == 2 and state.credits_gained == 8
    assert "领取任务「任务一」（进度开始计）" in state.parts
    assert "领取任务「T5」失败：prerequisite not met" in state.parts
    assert "领任务奖「T3」+credit8+energy2" in state.parts


def test_heatmap_scores_parses_and_validates():
    """日历解析: 同日同分合并;冲突/负分/NaN/坏日期/缺结构都要显式抛 ValueError。"""
    today, scores = rewards.heatmap_scores({
        "today": {"date": "2026-10-21"},
        "cells": [
            {"date": "2026-10-20", "score": 0},
            {"date": "2026-10-21", "score": 2},
            {"date": "2026-10-21", "score": 2},
        ],
    })
    assert today == date(2026, 10, 21)
    assert scores == {date(2026, 10, 20): 0, date(2026, 10, 21): 2}
    broken = [
        {"today": {"date": "2026-10-21"}},
        {"today": {"date": "2026-10-21"}, "cells": ["x"]},
        {"today": {"date": "2026-10-21"},
         "cells": [{"date": "2026-1-01", "score": 1}]},
        {"today": {"date": "2026-10-21"},
         "cells": [{"date": "2026-10-20", "score": -1}]},
        {"today": {"date": "2026-10-21"},
         "cells": [{"date": "2026-10-20", "score": float("nan")}]},
        {"today": {"date": "2026-10-21"},
         "cells": [{"date": "2026-10-20", "score": 1},
                   {"date": "2026-10-20", "score": 2}]},
    ]
    for body in broken:
        with pytest.raises(ValueError):
            rewards.heatmap_scores(body)


def test_makeup_candidates_picks_latest_broken_days():
    """补登候选: 当月/今天之前/上线之后/score==0/未补过,最近的断点排最前。"""
    streak = {"launch_date": "2026-06-20", "streak": {"makeup_dates": ["2026-10-10"]}}
    heatmap = {
        "today": {"date": "2026-10-21"},
        "cells": [
            {"date": "2026-09-30", "score": 0},
            {"date": "2026-10-01", "score": 0},
            {"date": "2026-10-10", "score": 0},
            {"date": "2026-10-15", "score": 0},
            {"date": "2026-10-18", "score": 3},
            {"date": "2026-10-20", "score": 0},
            {"date": "2026-10-21", "score": 0},
        ],
    }
    assert rewards.makeup_candidates(streak, heatmap) == [
        "2026-10-20", "2026-10-15", "2026-10-01",
    ]
    late_launch = {"launch_date": "2026-10-12", "streak": {"makeup_dates": []}}
    assert rewards.makeup_candidates(late_launch, heatmap) == ["2026-10-20", "2026-10-15"]
    with pytest.raises(ValueError):
        rewards.makeup_candidates({"launch_date": "2026-06-20", "streak": "bad"}, heatmap)


def test_run_makeup_uses_card_marks_stale_tolerates_race():
    """有卡才查日历并补最近断点,成功置 stale;400「无需补登」是竞争成功不算失败。"""
    streak = {
        "launch_date": "2026-06-17", "makeup_cards": {"balance": 1},
        "streak": {"makeup_dates": []},
    }
    heatmap = {
        "today": {"date": "2026-10-21"},
        "cells": [{"date": "2026-10-19", "score": 0}, {"date": "2026-10-20", "score": 0}],
    }
    net = _ScriptedNet(
        gets=[(200, streak), (200, heatmap)],
        posts=[(200, {"makeup_cards": {"balance": 0}})],
    )
    state = growth.GrowthState()
    out = rewards.run_makeup(net, state, 10.0)
    assert out == streak
    uses = [p for m, s, p in net.calls if s.endswith("/makeup-cards/use")]
    assert uses == [{"target_date": "2026-10-20"}]
    assert state.stale is True
    assert state.parts == ["补登 2026-10-20（剩 0 张卡）"]

    raced = _ScriptedNet(
        gets=[(200, streak), (200, heatmap)],
        posts=[(400, {"msg": "Date is not broken, no makeup needed"})],
    )
    raced_state = growth.GrowthState()
    rewards.run_makeup(raced, raced_state, 10.0)
    assert raced_state.failures == 0
    assert raced_state.parts == ["2026-10-20 已活跃或已补登，无需再次补登"]

    broke = _ScriptedNet(gets=[(200, {**streak, "makeup_cards": 0})])
    broke_state = growth.GrowthState()
    rewards.run_makeup(broke, broke_state, 10.0)
    assert broke.calls == [("GET", rewards.GROWTH_BASE + "/streak", None)]


def test_run_redeem_retries_days_and_skips_locked():
    """claimed/locked 档不发起兑换;400 unknown tier 换天数重试;奖励描述优先实发字段。"""
    summary = {
        "starter_status": "claimed",
        "advanced_status": "available",
        "legendary_status": "locked",
    }
    net = _ScriptedNet(
        gets=[(200, summary)],
        posts=[
            (400, {"msg": "Unknown tier: 14d"}),
            (200, {"credit_granted": 50, "energy_granted": 3}),
        ],
    )
    state = growth.GrowthState()
    rewards.run_redeem(net, state, 10.0)
    tiers = [p["tier"] for m, s, p in net.calls if s.endswith("/redeem")]
    assert tiers == ["14d", 14]
    assert state.credits_gained == 50
    assert state.parts == ["连登兑换「进阶」（+50 积分 +3 能量）"]

    fallback = _ScriptedNet(gets=[(200, summary)], posts=[(200, {"credit_granted": 0})])
    fallback_state = growth.GrowthState()
    rewards.run_redeem(fallback, fallback_state, 10.0)
    assert fallback_state.parts == [
        "连登兑换「进阶」（+50 积分 +3 能量 +1 补登卡 +1 次抽奖）",
    ]
