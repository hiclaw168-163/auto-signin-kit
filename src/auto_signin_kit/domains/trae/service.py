"""Trae CN 每日签到: 状态查询 → 幂等领取 → 一行 JSON。

契约对齐 Trae CN 桌面端 ttnet 调用(逆向): status 先行,``checked_in=true``
直接回 ALREADY;领取 body 固定 ``{"Request": {}}``,写操作默认不重试。
"""

from __future__ import annotations

import json
from typing import Any

from auto_signin_kit.core.httpclient import BUDGET_OUT, NO_NETWORK, Budget
from auto_signin_kit.domains.trae.api import (
    SENSITIVE,
    Api,
    build_api,
    env_flag,
    log_path,
    parse_budget,
)
from auto_signin_kit.domains.trae.auth import NoAuthError, TraeAuthError

#: 空跑时默认不落盘的结果;TRAE_SIGNIN_LOG_EMPTY=1 可强制逐轮回写。
QUIET_RESULTS = ("ALREADY", "INACTIVE")


def _short(value: Any, limit: int = 160) -> str:
    """调试展示: 压扁成单行并截断。"""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return " ".join(text.split())[:limit]


def _body(payload: Any) -> dict:
    return payload if isinstance(payload, dict) else {}


def _transport_errors(code: int, payload: Any, err: str | None) -> dict | None:
    """HTTP/伪状态层统一映射;业务 code(1001 等)不在这里处理。"""
    if code in (401, 403):
        return {"result": "AUTH_REJECTED" if code == 401 else "FORBIDDEN",
                "needs_attention": True, "http": code,
                "report": ("登录态被服务端拒绝（HTTP %d），请打开 Trae CN 桌面端重新登录" % code
                           if code == 401 else
                           "被服务端拒绝（HTTP 403），请确认账号资格后重试")}
    if code == NO_NETWORK:
        return {"result": "NETWORK", "needs_attention": True,
                "report": "网络请求失败：%s" % _short(err)}
    if code == BUDGET_OUT:
        return {"result": "TIMEOUT", "needs_attention": True,
                "report": _body(payload).get("error") or "已达本次运行时间预算"}
    return None


def _business_error(code: int, payload: dict) -> dict | None:
    """业务码层映射;0=成功,1001=认证被拒,其余如实上报。"""
    if code == 0:
        return None
    return {"result": "ERROR", "needs_attention": True, "code": code,
            "report": "业务失败 code=%s：%s" % (code, _short(payload.get("message")) or "无消息")}


def check_status(api: Api, budget: Budget) -> dict:
    """查询并规整签到状态(不做修改)。"""
    code, payload, err = api.status(budget=budget)
    transport = _transport_errors(code, payload, err)
    if transport:
        return transport
    body = _body(payload)
    if code != 200:
        return {"result": "ERROR", "needs_attention": True,
                "report": "查询失败：HTTP %s %s" % (code, _short(payload))}
    if body.get("code") != 0:
        return {"result": "AUTH_REJECTED", "needs_attention": True,
                "report": "服务端无法认证（code=%s，%s）" % (body.get("code"),
                                                          _short(body.get("message")) or "无消息")}
    return {
        "result": "OK", "needs_attention": False,
        "checked_in": body.get("checked_in") is True,
        "credits": body.get("credits"),
        "extra_credits": body.get("extra_credits"),
        "report": "今日已签到（累计 %s Credits）" % body.get("credits")
                  if body.get("checked_in") else "今日尚未签到",
    }


def claim_once(api: Api, budget: Budget, *, dry_run: bool = False) -> dict:
    """status → (未签时)claim。结果: CLAIMED/ALREADY/DRY_RUN/AUTH_REJECTED/...。"""
    probe = check_status(api, budget)
    if probe.get("result") != "OK":
        return probe
    if dry_run:
        if probe.get("checked_in"):
            return {"result": "ALREADY", "needs_attention": False,
                    "credits": probe.get("credits"),
                    "report": "[dry-run] 今日已签到（%s Credits）" % probe.get("credits")}
        return {"result": "DRY_RUN", "needs_attention": False,
                "report": "[dry-run] 今日未签到,可领取（每日 %s + 附加 %s）"
                          % (probe.get("credits"), probe.get("extra_credits"))}
    if probe.get("checked_in"):
        return {"result": "ALREADY", "needs_attention": False,
                "credits": probe.get("credits"),
                "report": "今日已签到（%s Credits）" % probe.get("credits")}
    code, payload, err = api.claim(budget=budget)
    transport = _transport_errors(code, payload, err)
    if transport:
        return transport
    body = _body(payload)
    if code != 200:
        return {"result": "ERROR", "needs_attention": True,
                "report": "领取失败：HTTP %s %s" % (code, _short(payload))}
    business = _business_error(body.get("code") or 0, body)
    if business:
        return business
    if body.get("checked_in") is not True:
        return {"result": "ERROR", "needs_attention": True,
                "report": "领取已发出但服务端未确认签到：%s" % _short(payload)}
    credits = body.get("credits")
    extra = body.get("extra_credits")
    summary = "领取成功：今日 %s Credits" % ("" if credits is None else credits)
    if extra:
        summary += "（含活动附加 %s）" % extra
    return {"result": "CLAIMED", "needs_attention": False, "credits": credits,
            "extra_credits": extra or None, "report": summary}


def run_claim(api: Api, budget: Budget, *, dry_run: bool = False) -> tuple[dict, int]:
    """auto 主流程;退出码 0=成功或幂等,1=失败,2=不可用。"""
    out = claim_once(api, budget, dry_run=dry_run)
    if api.token_expires_at:
        out.setdefault("token_expires_at", api.token_expires_at)
    code = 0 if out.get("result") in ("CLAIMED", "ALREADY", "DRY_RUN") else 1
    return out, code


def run_status(api: Api, budget: Budget) -> tuple[dict, int]:
    """只查不领;结果 OK/AUTH_REJECTED/... 。"""
    out = check_status(api, budget)
    if api.token_expires_at:
        out.setdefault("token_expires_at", api.token_expires_at)
    code = 0 if out.get("result") == "OK" else 1
    return out, code


def run_action(
    action: str, config: Any, *, silent: bool = False, dry_run: bool = False, once: bool = False
) -> int:
    """命令分发;结果按一行 JSON 契约输出,退出码 0/1/2,本函数保证不抛业务异常。"""
    from auto_signin_kit.core.report import emit_result
    from auto_signin_kit.domains.trae.doctor import run_doctor

    del once  # 查询+领取共两次请求,预算内完成;once 保留参数位与 qoder 对齐
    if action not in ("claim", "status", "doctor"):
        emit_result({"result": "ERROR", "needs_attention": True,
                     "report": "未知命令：%s（可用：claim / status / doctor）" % action},
                    log_path=log_path(), log_errors_without_silent=False)
        return 2
    if action == "doctor":
        out, code = run_doctor(config.data_dir)
        emit_result(out, silent=silent, log_path=log_path(), sensitive=SENSITIVE,
                    log_errors_without_silent=False)
        return code

    budget_value, warning = parse_budget(config)
    budget = Budget(budget_value)
    try:
        api, info, staleness = build_api(config)
    except NoAuthError as exc:
        emit_result({"result": "NO_AUTH", "needs_attention": True, "report": str(exc),
                     "looked_in": exc.looked_in or None},
                    silent=silent, log_path=log_path(), log_errors_without_silent=False)
        return 2
    except TraeAuthError as exc:
        emit_result({"result": "AUTH_ERROR", "needs_attention": True, "report": str(exc),
                     "looked_in": exc.looked_in or None},
                    silent=silent, log_path=log_path(), log_errors_without_silent=False)
        return 1
    if staleness == "EXPIRED":
        emit_result({"result": "AUTH_REJECTED", "needs_attention": True,
                     "token_expires_at": info.get("expiredAt") if info else None,
                     "report": "登录态已过期，请打开 Trae CN 桌面端重新登录"},
                    silent=silent, log_path=log_path(), log_errors_without_silent=False)
        return 1
    del info

    if action == "status":
        out, code = run_status(api, budget)
    else:  # claim
        out, code = run_claim(api, budget, dry_run=dry_run)
    if warning:
        out.setdefault("config_warning", warning)
    emit_result(out, silent=silent, log_path=log_path(), sensitive=SENSITIVE,
                quiet=bool(out.get("result") in QUIET_RESULTS
                           and not env_flag("TRAE_SIGNIN_LOG_EMPTY")),
                log_errors_without_silent=False)
    return code
