"""成长中心 · 第二段: 补登卡(保连登) 与 连登奖励兑换(三档)。

显式顺序: 补登在兑换之前——补登会改变连登天数,先补,兑换才能拿到最新解锁状态。
规则对齐官方成长中心(2026-10-01 核对):补登卡上限 4 张、仅可补救当月断登;
/streak 的 makeup_dates 是已补登历史,真正的断点来自 /heatmap 中 score==0 的历史日期。
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

from auto_signin_kit.domains.workbuddy.fields import (
    as_int,
    client_token,
    dig,
    fmt_credit,
)
from auto_signin_kit.domains.workbuddy.growth import (
    GROWTH_BASE,
    GrowthState,
    StopRoundError,
    check_auth,
)
from auto_signin_kit.domains.workbuddy.net import Net

#: 每轮最多用掉一张补登卡;仅依据服务端活跃日历中明确的当月断登日期操作。
MAKEUP_MAX_PER_RUN = 1

#: 连登兑换三档: (档位标识, /redeem/summary 状态字段前缀, 展示名, 天数)。
_REDEEM_TIERS: tuple[tuple[str, str, str, int], ...] = (
    ("7d", "starter", "入门", 7),
    ("14d", "advanced", "进阶", 14),
    ("28d", "legendary", "巅峰", 28),
)
#: 服务端没给实发明细时回落的活动文案(2026-09 版本)。
_REDEEM_REWARDS = {
    "7d": "+2 能量 +1 补登卡 +1 次抽奖",
    "14d": "+50 积分 +3 能量 +1 补登卡 +1 次抽奖",
    "28d": "+150 积分 +5 能量 +1 补登卡 +1 次抽奖",
}


def _parse_date(value: Any) -> date:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError("补登日期格式无效")
    return date.fromisoformat(value)


def heatmap_scores(heatmap_body: dict) -> tuple[date, dict[date, Any]]:
    """解析活跃日历 → ``(服务端今天, {日期: score})``;结构异常直接抛 ValueError。

    score 即当天的活跃记录: **只有完成过一次对话才会被记分**,签到本身不计。
    今天取服务端日期而非本机日期——本机时区会把月初/午夜算错一天。
    日历缺项不推测为零;分数重复且不一致视为结构异常。
    """
    today_obj = dig(heatmap_body, "today")
    if not isinstance(today_obj, dict):
        raise ValueError("活跃日历缺少服务端日期")
    today = _parse_date(today_obj.get("date"))
    cells = dig(heatmap_body, "cells")
    if not isinstance(cells, list):
        raise ValueError("活跃日历缺少日期列表")
    scores: dict[date, Any] = {}
    for cell in cells:
        if not isinstance(cell, dict):
            raise ValueError("活跃日历记录格式无效")
        day = _parse_date(cell.get("date"))
        score = cell.get("score")
        if type(score) not in (int, float) or not math.isfinite(score) or score < 0:
            raise ValueError("活跃日历分数无效")
        if day in scores and scores[day] != score:
            raise ValueError("活跃日历同一日期的记录冲突")
        scores[day] = score
    return today, scores


def today_activity(heatmap_body: dict) -> dict:
    """判断服务端"今天"是否已产生活跃记录(即今天是否完成过一次对话)。

    活跃天数只认对话: 领了积分没对话,当天 score 仍是 0,连续活跃天数会断。
    这个命令存在的意义就是把"静默断签"变成"当天就能看见的提醒"。
    """
    today, scores = heatmap_scores(heatmap_body)
    score = scores.get(today)
    day = today.isoformat()
    if score is None:
        return {
            "result": "NO_DATA",
            "needs_attention": True,
            "date": day,
            "report": "活跃日历里没有今天（%s）的记录，无法判断活跃状态" % day,
        }
    if score > 0:
        return {
            "result": "ACTIVE",
            "needs_attention": False,
            "date": day,
            "score": score,
            "report": "今天（%s）已有活跃记录（score=%s），连续活跃天数未中断" % (day, score),
        }
    return {
        "result": "NO_ACTIVITY",
        "needs_attention": True,
        "date": day,
        "score": 0,
        "report": "今天（%s）还没有活跃记录：请在 WorkBuddy 里完成一次对话，"
                  "否则连续活跃天数会中断（仅领积分不算活跃）" % day,
    }


def run_active(net: Net) -> dict:
    """查活跃日历并报告今天的活跃状态;失败按一行 JSON 契约返回,不抛给上层。"""
    if net.budget_remaining() <= 0:
        return {"result": "ERROR", "needs_attention": True, "report": "时间预算耗尽，活跃检查跳过"}
    try:
        code, body = net.get(GROWTH_BASE + "/heatmap")
        check_auth(code)
        if not 200 <= code < 300:
            return {
                "result": "ERROR",
                "needs_attention": True,
                "report": "查活跃日历失败：HTTP %s" % code,
            }
        return today_activity(body)
    except StopRoundError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {
            "result": "ERROR",
            "needs_attention": True,
            "report": "活跃检查异常（%s: %s）" % (type(exc).__name__, exc),
        }


def makeup_candidates(streak_body: dict, heatmap_body: dict) -> list[str]:
    """从日历选断登日期;makeup_dates 是已补登历史,不能作为待办清单。

    当月、今天之前、活动上线之后、score==0;heatmap.today 用服务端日期,
    避免本机时区把月初/午夜算错。日历缺项不推测为零;结构异常整段停止。
    """
    today, scores = heatmap_scores(heatmap_body)
    launch = _parse_date(dig(streak_body, "launch_date"))
    start = max(today.replace(day=1), launch, date(2026, 6, 17))
    streak = dig(streak_body, "streak")
    if not isinstance(streak, dict):
        raise ValueError("连登状态格式无效")
    made_up_raw = streak.get("makeup_dates", [])
    if not isinstance(made_up_raw, list):
        raise ValueError("已补登记录格式无效")
    made_up = {_parse_date(v) for v in made_up_raw}
    # 最近的断点优先,尽早恢复当前连登;每轮仍受 MAKEUP_MAX_PER_RUN 限制。
    return sorted(
        (day.isoformat() for day, score in scores.items()
         if start <= day < today and score == 0 and day not in made_up),
        reverse=True,
    )


def run_makeup(net: Net, state: GrowthState, budget_left: float) -> dict | None:
    """补登卡: 查连登 → (有卡时)查日历 → 补最近的断点;返回本轮的 /streak 数据。

    makeup_cards 兼容 ``{"balance": n}`` 与纯数字两种形状;查询失败必须提醒,
    不能把未知状态解释为"无需补登"。
    """
    if budget_left <= 0:
        state.note("时间预算耗尽，补登跳过")
        return None
    try:
        mcode, mbody = net.get(GROWTH_BASE + "/streak")
        check_auth(mcode)
        if state.note_http(mcode, mbody, "查连登状态", required=True):
            return None
        cards_obj = dig(mbody, "makeup_cards")
        if isinstance(cards_obj, dict):
            cards = as_int(cards_obj.get("balance"))
        else:
            cards = as_int(cards_obj)
        if cards > 0:
            hcode, hbody = net.get(GROWTH_BASE + "/heatmap")
            check_auth(hcode)
            if not state.note_http(hcode, hbody, "查补登日历", required=True):
                dates = makeup_candidates(mbody, hbody)
                _use_cards(net, state, dates, cards, budget_left)
        return mbody
    except StopRoundError:
        raise
    except Exception as exc:  # noqa: BLE001
        state.fail("补登模块异常（%s: %s）" % (type(exc).__name__, exc), -1)
        return None


def _use_cards(
    net: Net, state: GrowthState, dates: list[str], cards: int, budget_left: float
) -> None:
    made_up = 0
    for target in dates[: min(cards, MAKEUP_MAX_PER_RUN)]:
        if budget_left <= 0:
            state.note("时间预算耗尽，剩余补登下次再做")
            break
        ucode, ubody = net.post(GROWTH_BASE + "/makeup-cards/use", {"target_date": target})
        check_auth(ucode)
        if _api_ok(ucode, ubody):
            made_up += 1
            state.stale = True  # 补登成功会改变连签天数,展示值必须重新取
            left_obj = dig(ubody, "makeup_cards")
            if isinstance(left_obj, dict):
                left = as_int(left_obj.get("balance"), cards)
            else:
                left = as_int(left_obj, cards)
            state.ok("补登 %s（剩 %s 张卡）" % (target, left), 0)
            cards = max(0, left)
        else:
            msg = str(dig(ubody, "msg") or "")
            if ucode == 400 and msg.strip().lower() == "date is not broken, no makeup needed":
                # 查询后已被客户端/另一轮补上是正常竞争,不再当失败刷日志。
                state.note("%s 已活跃或已补登，无需再次补登" % target)
                break
            state.fail("补登 %s 失败：%s" % (target, msg or "HTTP %s" % ucode), ucode)
            break
    if made_up and len(dates) > made_up and cards > 0:
        state.note("另有 %s 天可补、剩 %s 张卡，下轮继续" % (len(dates) - made_up, cards))


def _api_ok(code: int, body: Any) -> bool:
    business = body.get("code") if isinstance(body, dict) else None
    return 200 <= code < 300 and (
        business is None or (type(business) is int and business == 0)
    )


def _is_unknown_tier(code: int, body: Any) -> bool:
    """tier 值被判不认识: 换一种写法(天数)再试一次;这类 400 没兑换任何东西。"""
    if code != 400:
        return False
    msg = str(dig(body, "msg") or "").lower()
    return "tier" in msg and ("unknown" in msg or "unsupported" in msg or "invalid" in msg)


def _is_tier_locked(code: int, body: Any) -> bool:
    """未解锁档位(403 + 天数不足): 常态,不是故障,与认证拒绝严格区分。"""
    if code != 403:
        return False
    msg = str(dig(body, "msg") or "")
    return "天数不足" in msg or "不足" in msg


def _redeem_reward_desc(body: dict, tier: str) -> str:
    """兑换奖励描述: 优先服务端实发(带 _granted 后缀),挖不到回落官方文案。"""
    bits = []
    for key, label in (
        ("credit_granted", "积分"), ("energy_granted", "能量"),
        ("cards_granted", "补登卡"), ("chances_granted", "次抽奖"),
    ):
        value = as_int(dig(body, key), 0)
        if value:
            bits.append("+%s %s" % (fmt_credit(value), label))
    if bits:
        return "（%s）" % " ".join(bits)
    return "（%s）" % _REDEEM_REWARDS.get(tier, "奖励已到账")


def run_redeem(net: Net, state: GrowthState, budget_left: float) -> None:
    """连登兑换: 三档遍历;非 claimed/locked 的状态才去尝试兑换。"""
    if budget_left <= 0:
        state.note("时间预算耗尽，连登兑换跳过")
        return
    try:
        rcode, rbody = net.get(GROWTH_BASE + "/redeem/summary")
        check_auth(rcode)
        if state.note_http(rcode, rbody, "查连登兑换"):
            return
        for tier, status_key, label, days in _REDEEM_TIERS:
            if budget_left <= 0:
                state.note("时间预算耗尽，剩余连登兑换下次再领")
                break
            status = dig(rbody, status_key + "_status")
            # 字段缺失同样跳过: 接口改版时不该对三档无脑 POST。
            if not status or status in ("claimed", "locked"):
                continue
            c2code, c2body = net.post(
                GROWTH_BASE + "/redeem", {"tier": tier, "client_token": client_token()}
            )
            if _is_unknown_tier(c2code, c2body):
                c2code, c2body = net.post(
                    GROWTH_BASE + "/redeem",
                    {"tier": days, "client_token": client_token()},
                )
            # 403「天数不足」要先于认证拒绝判断,否则未解锁档位会误终止整轮。
            if _is_tier_locked(c2code, c2body):
                state.note("连登兑换「%s」未解锁（连登天数不足）" % label)
                continue
            check_auth(c2code)
            if 200 <= c2code < 300:
                gained = as_int(dig(c2body, "credit_granted"), 0)
                state.ok("连登兑换「%s」%s" % (label, _redeem_reward_desc(c2body, tier)), gained)
            else:
                msg = dig(c2body, "msg") or ""
                state.fail("连登兑换「%s」失败：%s" % (label, msg or "HTTP %s" % c2code), c2code)
    except StopRoundError:
        raise
    except Exception as exc:  # noqa: BLE001
        state.fail("连登兑换模块异常（%s: %s）" % (type(exc).__name__, exc), -1)
