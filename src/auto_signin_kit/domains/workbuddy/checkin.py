"""WorkBuddy 每日签到: 查状态 → 未签才领 → 最新连签/累计数 → 一行汇报。"""

from __future__ import annotations

from typing import Any

from auto_signin_kit.core.httpclient import BUDGET_OUT, NO_NETWORK
from auto_signin_kit.domains.workbuddy.fields import dig, fmt_credit
from auto_signin_kit.domains.workbuddy.net import Net

STATUS_SUFFIX = "/v2/billing/meter/checkin-activity-status"
CLAIM_SUFFIX = "/v2/billing/meter/daily-checkin"


def auth_failure_dto(code: int) -> dict[str, Any]:
    """服务端认证拒绝(401)/权限拒绝(403)的统一结果。"""
    if code == 401:
        return {
            "result": "AUTH_REJECTED",
            "http": code,
            "needs_attention": True,
            "report": "服务端拒绝认证（HTTP 401），请检查客户端登录状态、凭据及脚本版本",
        }
    return {
        "result": "FORBIDDEN",
        "http": code,
        "needs_attention": True,
        "report": "服务端拒绝此操作（HTTP 403），请检查账号权限或活动条件；不能据此判断登录过期",
    }


def is_already_checked_in(cbody: Any) -> bool:
    """领取接口返回是否表示"今日已签"(兼容 None 与 400+code 10001 两种形态)。"""
    if cbody is None:
        return True
    if isinstance(cbody, dict):
        msg = str(cbody.get("msg") or "")
        if cbody.get("code") == 10001 or "已签" in msg:
            return True
    return False


def _already(status: Any, via: str | None = None) -> dict[str, Any]:
    """根据状态构造"今日已签"汇报 dict。"""
    today_credit = dig(status, "today_credit") or dig(status, "daily_credit")
    streak_days = dig(status, "streak_days")
    total_credits = dig(status, "total_credits")
    is_streak_day = dig(status, "is_streak_day")
    next_streak_day = dig(status, "next_streak_day")
    inner = []
    if today_credit is not None:
        inner.append("今日 +%s" % fmt_credit(today_credit))
    if streak_days is not None:
        inner.append("连续 %s 天" % streak_days)
    if total_credits is not None:
        inner.append("累计 %s 积分" % fmt_credit(total_credits))
    prefix = via or "今日已签过"
    report = "%s（%s）" % (prefix, "，".join(inner)) if inner else prefix
    return {
        "result": "ALREADY",
        "report": report,
        "today_credit": today_credit,
        "streak_days": streak_days,
        "total_credits": total_credits,
        "is_streak_day": is_streak_day,
        "next_streak_day": next_streak_day,
    }


def _reach_failure(status: int, body: Any) -> dict[str, Any] | None:
    """状态查询前置失败(预算/网络)的统一结果;None 表示请求已正常送达。"""
    if status == BUDGET_OUT:
        return {"result": "TIMEOUT", "report": "已达本次运行时间预算，签到跳过，下次自动重试"}
    if status == NO_NETWORK:
        detail = (body or {}).get("error") or ""
        return {
            "result": "NETWORK",
            "report": "网络不可达，签到跳过，下次自动重试（%s）" % detail,
            "error": detail,
        }
    return None


def run_auto(net: Net) -> tuple[int, dict[str, Any]]:
    """每日自动化主逻辑: 查状态 → 未签才领 → 返回 (退出码, 结果)。"""
    scode, sbody = net.post(STATUS_SUFFIX, retry=True)
    reach = _reach_failure(scode, sbody)
    if reach:
        return 1, reach
    if scode in (401, 403):
        return 1, auth_failure_dto(scode)
    if not 200 <= scode < 300:
        return 1, {
            "result": "ERROR",
            "report": "签到接口返回异常（HTTP %s），请重新登录客户端或稍后重试" % scode,
            "http": scode,
            "status_body": sbody,
        }
    status = sbody if isinstance(sbody, dict) else {}
    if dig(status, "active") is False:
        activity_name = dig(status, "activity_name")
        return 0, {
            "result": "INACTIVE",
            "report": "签到活动未开启" + ("（%s）" % activity_name if activity_name else ""),
            "active": False,
        }
    if dig(status, "today_checked_in") in (True, 1):
        return 0, _already(status)

    ccode, cbody = net.post(CLAIM_SUFFIX, retry=True)
    if ccode in (NO_NETWORK, BUDGET_OUT):
        label = "NETWORK" if ccode == NO_NETWORK else "TIMEOUT"
        detail = (cbody or {}).get("error") if isinstance(cbody, dict) else ""
        return 1, {
            "result": label,
            "report": "领取请求未能送达，下次自动重试（%s）" % (detail or ""),
        }
    # 认证/权限拒绝先于"已签"判断。
    if ccode in (401, 403):
        return 1, auth_failure_dto(ccode)
    if is_already_checked_in(cbody):
        scode2, sbody2 = net.post(STATUS_SUFFIX, retry=True)
        fresh = sbody2 if 200 <= scode2 < 300 and isinstance(sbody2, dict) else status
        return 0, _already(fresh, via="今日已签过（服务端判定已领取）")
    credit = dig(cbody, "credit")
    if credit is not None:
        return 0, _claimed_report(net, credit, status)
    if isinstance(cbody, dict) and ("code" in cbody or "msg" in cbody):
        msg = cbody.get("msg") or ("code %s" % cbody.get("code"))
        return 1, {
            "result": "ERROR",
            "report": "领取失败：%s（HTTP %s）" % (msg, ccode),
            "http": ccode,
            "claim_body": cbody,
        }
    return 1, {
        "result": "UNKNOWN",
        "report": "未识别的领取返回，请检查接口：%s" % _short_json(cbody),
        "http": ccode,
        "claim_body": cbody,
    }


def _short_json(body: Any, limit: int = 200) -> str:
    """调试展示: 响应压扁成单行并截断,不让任何一行汇报失控。"""
    import json

    try:
        text = json.dumps(body, ensure_ascii=False)
    except Exception:
        text = repr(body)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _claimed_report(net: Net, credit: Any, status: dict) -> dict[str, Any]:
    """领取成功: 再查一次状态,用最新字段组装连签/累计汇报。"""
    scode2, sbody2 = net.post(STATUS_SUFFIX, retry=True)
    fresh = sbody2 if 200 <= scode2 < 300 and isinstance(sbody2, dict) else status
    streak_days = dig(fresh, "streak_days") or dig(status, "streak_days")
    total_credits = dig(fresh, "total_credits")
    is_streak_day = dig(fresh, "is_streak_day")
    next_streak_day = dig(fresh, "next_streak_day")
    bonus = "，且为连签奖励日" if is_streak_day else ""
    cum = "，累计 %s 积分" % fmt_credit(total_credits) if total_credits is not None else ""
    # streak_days 缺失时不要把 None 打进文案。
    if streak_days is not None:
        streak = "（连续 %s 天%s）" % (streak_days, cum)
    else:
        streak = ("（%s）" % cum.lstrip("，")) if cum else ""
    return {
        "result": "CLAIMED",
        "report": "成功领取 %s 积分%s%s" % (fmt_credit(credit), bonus, streak),
        "credit": credit,
        "streak_days": streak_days,
        "total_credits": total_credits,
        "is_streak_day": is_streak_day,
        "next_streak_day": next_streak_day,
    }
