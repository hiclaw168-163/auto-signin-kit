"""Qoder CN 每日 Credits: 活动查询 → 分类 → 幂等领取 → 一行 JSON。

对策见 NOTE-02(qoder 积分面取证)的结论: /sash/api/v1/me/campaigns;
缺 ``Cosy-ClientType: 10`` 头服务端返回空列表;领取 POST 默认不重试防重复。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from auto_signin_kit.core.httpclient import BUDGET_OUT, NO_NETWORK, Budget
from auto_signin_kit.domains.qoder.api import (
    SENSITIVE,
    Api,
    build_api,
    log_path,
    parse_budget,
)
from auto_signin_kit.domains.qoder.auth import TZ_CN, NoAuthError, QoderAuthError


def _short(value: Any, limit: int = 200) -> str:
    """调试展示: 压扁成单行并截断。"""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _campaigns(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("campaigns")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _benefit(campaign: dict) -> dict:
    """benefit 子结构;缺失时给空 dict 免得调用处二次判型。"""
    benefit = campaign.get("benefit")
    return benefit if isinstance(benefit, dict) else {}


def _benefit_amount(campaign: dict) -> Any:
    """Credits 数额;benefit 缺失/形状异常时给 None(调用方回落展示)。"""
    amount = _benefit(campaign).get("amount")
    return amount if isinstance(amount, (int, float)) else None


def _validity(campaign: dict) -> str:
    validity = _benefit(campaign).get("validity")
    relative = (
        isinstance(validity, dict)
        and validity.get("mode") == "RELATIVE_DAYS"
        and validity.get("days")
    )
    if relative:
        return "领取后 %s 天有效" % _benefit(campaign)["validity"]["days"]
    return ""


def is_active(campaign: dict, now: float) -> bool:
    """活动窗口(时间戳)是否包含 now;缺字段视为生效。"""
    start, end = campaign.get("startAt"), campaign.get("endAt")
    if isinstance(start, (int, float)) and now < start:
        return False
    return not (isinstance(end, (int, float)) and now >= end)


def _fmt_ts(value: Any) -> str:
    """时间戳 → UTC+8 人话;缺失/非数返回空串。"""
    if not isinstance(value, (int, float)):
        return ""
    if value > 10**12:  # 毫秒换秒
        value = value / 1000.0
    return datetime.fromtimestamp(value, TZ_CN).strftime("%m-%d %H:%M")


def _window(campaign: dict) -> str:
    """活动窗口的人话展示。"""
    start, end = _fmt_ts(campaign.get("startAt")), _fmt_ts(campaign.get("endAt"))
    if start and end:
        return "%s ~ %s" % (start, end)
    return ""


def _campaign_key(campaign: dict) -> str:
    return str(campaign.get("campaignKey") or campaign.get("campaignId") or "?")


def select_campaigns(payload: Any, now: float) -> dict[str, list[dict]]:
    """把活动列表分成: 可领取(todo) / 窗内已领(claimed) / 窗内其它(active)。"""
    benefit = [c for c in _campaigns(payload) if c.get("actionType") == "CLAIM_BENEFIT"]
    active = [c for c in benefit if is_active(c, now)]
    return {
        "benefit": benefit,
        "active": active,
        "todo": [c for c in active if c.get("claimStatus") == "CLAIMABLE"],
        "claimed": [c for c in active if c.get("claimStatus") == "CLAIMED"],
    }


def _simplify_amount(values: list) -> Any:
    """一批 Credits 数额求和;全缺失时 None(避免虚报 0)。"""
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def claim_all(api: Api, campaigns: list[dict], budget: Budget) -> dict:
    """逐个领取已分类的可领取项;任一 401/403 直接终止(换凭据才有意义)。"""
    total: Any = None
    keys: list[str] = []
    failures: list[str] = []
    for campaign in campaigns:
        key = _campaign_key(campaign)
        code, body, err = api.claim(campaign.get("campaignId"), budget=budget, retry=False)
        if code in (401, 403):
            # 401=登录态被拒(必须重新登录);403=资格问题;两者继续领后面的也无意义。
            result = "AUTH_REJECTED" if code == 401 else "FORBIDDEN"
            reason = (
                "登录态被拒绝（HTTP 401），请打开 Qoder CN 桌面端重新登录"
                if code == 401
                else "领取被服务端拒绝（HTTP 403），请确认账号资格后重试"
            )
            return {"result": result, "needs_attention": True, "http": code,
                    "campaign": key, "report": reason}
        data = (
            body["data"] if isinstance(body, dict) and isinstance(body.get("data"), dict) else body
        )
        ok = code == 200 and isinstance(data, dict) and data.get("status") == "CLAIMED"
        if not ok:
            failures.append("%s: HTTP %s %s" % (key, code, _short(err or body, 120)))
            continue
        amount = _benefit_amount(data) if isinstance(data, dict) else None
        if amount is None:
            amount = _benefit_amount(campaign)
        total = amount if total is None else (total + (amount or 0))
        keys.append(key)
    if failures:
        return {"result": "ERROR", "needs_attention": True, "campaign": ", ".join(keys) or None,
                "credits": total, "report": "领取失败：%s" % "；".join(failures)}
    report = "成功领取 %s Credits" % ("" if total is None else total)
    if keys:
        report += "（%s）" % "、".join(keys)
    validity = _validity(campaigns[0]) if campaigns else ""
    if validity:
        report += "，" + validity
    return {"result": "CLAIMED", "needs_attention": False, "campaign": ", ".join(keys) or None,
            "credits": total, "report": report}


def _claim_picked(api: Api, picked: list[dict], budget: Budget, dry_run: bool) -> dict:
    keys = "、".join(_campaign_key(c) for c in picked)
    amount = _simplify_amount([_benefit_amount(c) for c in picked])
    if dry_run:
        return {"result": "DRY_RUN", "needs_attention": False, "campaign": keys, "credits": amount,
                "report": "[dry-run] 可领取 %s Credits（%s）"
                          % ("" if amount is None else amount, keys)}
    out = claim_all(api, picked, budget)
    if api.token_expires_at:
        out.setdefault("token_expires_at", api.token_expires_at)
    return out


def _auth_dto(code: int) -> dict:
    if code == 401:
        return {"result": "AUTH_REJECTED", "needs_attention": True, "http": code,
                "report": "登录态被服务端拒绝（HTTP 401），请打开 Qoder CN 桌面端重新登录"}
    return {"result": "FORBIDDEN", "needs_attention": True, "http": code,
            "report": "被服务端拒绝（HTTP 403），请确认账号资格后重试"}


def claim_once(api: Api, budget: Budget, *, retry: bool = True, dry_run: bool = False) -> dict:
    """查询 → 分类 → 领取/汇报。结果: CLAIMED/ALREADY/PENDING/INACTIVE/DRY_RUN。"""
    code, payload, err = api.campaigns(budget=budget, retry=retry)
    if code in (401, 403):
        return _auth_dto(code)
    if code == NO_NETWORK:
        return {"result": "NETWORK", "needs_attention": True,
                "report": "网络请求失败：%s" % _short(err)}
    if code == BUDGET_OUT:
        return {"result": "TIMEOUT", "needs_attention": True,
                "report": (payload or {}).get("error", "已达本次运行时间预算")}
    if code != 200 or not isinstance(payload, dict):
        return {"result": "ERROR", "needs_attention": True,
                "report": "查询活动失败：HTTP %s %s" % (code, _short(payload))}
    picked = select_campaigns(payload, time.time())
    if picked["todo"]:
        return _claim_picked(api, picked["todo"], budget, dry_run)
    if picked["claimed"]:
        keys = "、".join(_campaign_key(c) for c in picked["claimed"])
        window = _window(picked["claimed"][0])
        suffix = "，窗口 %s" % window if window else ""
        amount = _simplify_amount([_benefit_amount(c) for c in picked["claimed"]])
        return {"result": "ALREADY", "needs_attention": False, "campaign": keys,
                "credits": amount,
                "report": "本周期已领取 %s Credits（%s%s）"
                          % ("" if amount is None else amount, keys, suffix)}
    if picked["active"]:
        rows = "；".join(
            "%s status=%s" % (_campaign_key(c), c.get("claimStatus") or "?")
            for c in picked["active"]
        )
        return {"result": "PENDING", "needs_attention": True,
                "report": "活动存在但当前不可领取：%s" % rows}
    return {"result": "INACTIVE", "needs_attention": False,
            "report": "当前没有可领取的 Credits 活动（活动可能已结束）"}


def run_status(api: Api, budget: Budget) -> tuple[dict, int]:
    """只查活动列表不领取;输出每个活动的状态快照。"""
    code, payload, err = api.campaigns(budget=budget, retry=True)
    if code in (401, 403):
        return _auth_dto(code), 1
    if code == NO_NETWORK:
        return {"result": "NETWORK", "needs_attention": True,
                "report": "网络请求失败：%s" % _short(err)}, 1
    if code == BUDGET_OUT:
        return {"result": "TIMEOUT", "needs_attention": True,
                "report": (payload or {}).get("error", "已达本次运行时间预算")}, 1
    if code != 200 or not isinstance(payload, dict):
        return {"result": "ERROR", "needs_attention": True,
                "report": "查询活动失败：HTTP %s %s" % (code, _short(payload))}, 1
    now = time.time()
    rows = [
        {
            "campaign": _campaign_key(c),
            "action": c.get("actionType"),
            "status": c.get("claimStatus"),
            "credits": _benefit_amount(c),
            "active": is_active(c, now),
            "window": _window(c) or None,
            "validity": _validity(c) or None,
        }
        for c in _campaigns(payload)
    ]
    return {
        "result": "OK", "needs_attention": False, "campaigns": rows,
        "show_campaign": payload.get("showCampaign"), "claimable": payload.get("claimable"),
        "report": "共 %s 个活动" % len(rows),
    }, 0


def run_action(
    action: str, config: Any, *, silent: bool = False, dry_run: bool = False, once: bool = False,
    command: str | None = None,
) -> int:
    """命令分发;结果按一行 JSON 契约输出,退出码 0/1/2,本函数保证不抛业务异常。

    ``command`` 是用户/调度器实际敲的 CLI 命令名(如 ``auto``),用于台账
    ``action`` 标识;缺省回落到内部 action 名。
    """
    from auto_signin_kit.core.report import emit_result
    from auto_signin_kit.domains.qoder.doctor import run_doctor

    label = command or action  # 台账/日志里的动作标识
    if action not in ("claim", "status", "doctor"):
        emit_result({"domain": "qoder", "result": "ERROR", "needs_attention": True,
                     "report": "未知命令：%s（可用：claim / status / doctor）" % action},
                    action=label, log_path=log_path(), log_errors_without_silent=False)
        return 2
    if action == "doctor":
        out, code = run_doctor()
        emit_result(out, domain="qoder", action=label, silent=silent, log_path=log_path(),
                    sensitive=SENSITIVE, log_errors_without_silent=False)
        return code

    budget_value, warning = parse_budget(config)
    budget = Budget(budget_value)
    try:
        api, sess, staleness = build_api(config)
    except NoAuthError as exc:
        emit_result({"domain": "qoder", "result": "NO_AUTH",
                     "needs_attention": True, "report": str(exc),
                     "looked_in": exc.looked_in or None},
                    action=label, silent=silent, log_path=log_path(),
                    log_errors_without_silent=False)
        return 2
    except QoderAuthError as exc:
        emit_result({"domain": "qoder", "result": "AUTH_ERROR",
                     "needs_attention": True, "report": str(exc),
                     "looked_in": exc.looked_in or None},
                    action=label, silent=silent, log_path=log_path(),
                    log_errors_without_silent=False)
        return 1
    if staleness == "EXPIRED":
        emit_result({"domain": "qoder", "result": "AUTH_REJECTED", "needs_attention": True,
                     "token_expires_at": sess.get("expiresAt") if sess else None,
                     "report": "登录态已过期，请打开 Qoder CN 桌面端重新登录"},
                    action=label, silent=silent, log_path=log_path(),
                    log_errors_without_silent=False)
        return 1

    if action == "status":
        out, code = run_status(api, budget)
    else:  # claim
        out = claim_once(api, budget, retry=not once, dry_run=dry_run)
        if api.token_expires_at:
            out.setdefault("token_expires_at", api.token_expires_at)
        code = 0 if out.get("result") in ("CLAIMED", "ALREADY", "INACTIVE", "DRY_RUN") else 1
    if warning:
        out.setdefault("config_warning", warning)
    emit_result(out, domain="qoder", action=label, silent=silent, log_path=log_path(),
                sensitive=SENSITIVE, log_errors_without_silent=False)
    return code
