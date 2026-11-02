"""doubao 域业务逻辑: 签到领取/活动查询/体检。

**禁止 import typer**;交互走 ``core.prompt`` 注入,输出走 ``core.report``。
接口契约见 ``api.py`` 模块 docstring 与 docs/notes/NOTE-04。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.httpclient import NO_NETWORK, Budget
from auto_signin_kit.core.logging import result_log_path
from auto_signin_kit.core.report import emit_result, make_result
from auto_signin_kit.domains.doubao import cookies
from auto_signin_kit.domains.doubao.api import DoubaoApi
from auto_signin_kit.domains.doubao.schema import DoubaoDomainConfig

#: 本域支持的内部动作;``auto`` 是 CLI 主入口的别名。
KNOWN_ACTIONS = ("claim", "status", "doctor")


def _log_path() -> Any:
    return result_log_path("doubao", env_var="AUTO_SIGNIN_KIT_LOG_DIR")


def _user_data_dir(config: DoubaoDomainConfig) -> Path | None:
    return Path(config.user_data_dir) if config.user_data_dir else None


def _build_api(config: DoubaoDomainConfig, *, once: bool) -> DoubaoApi:
    cookie_header = cookies.load_cookie_header(_user_data_dir(config))
    budget = Budget(config.budget_seconds or 240.0)
    return DoubaoApi(cookie_header, base_url=config.base_url,
                     budget=budget, retry=not once)


def _emit(out: dict[str, Any], *, action: str, silent: bool) -> None:
    emit_result(out, domain="doubao", action=action, silent=silent,
                log_path=_log_path(), log_errors_without_silent=False)


def _checkin(api: DoubaoApi, config: DoubaoDomainConfig, *, dry_run: bool,
             action: str, silent: bool) -> int:
    """签到主流程;幂等接口,重复运行返回已签状态。"""
    if dry_run:
        scode, body, err = api.landing_page(config.campaign_code)
        ok = scode == 200 and isinstance(body, dict) and body.get("code") == 0
        out = make_result(
            "DRY_RUN" if ok else "ERROR",
            "空跑: 活动落地页可达" if ok else "空跑失败: 网络/服务异常 %s" % (err or scode),
            dry_run=True, domain="doubao", http=scode, needs_attention=not ok)
        _emit(out, action=action, silent=silent)
        return 0 if ok else 1

    scode, body, err = api.checkin(config.campaign_code)
    if scode == NO_NETWORK:
        out = make_result("NETWORK", "网络不可达: %s" % (err or "unknown"),
                          needs_attention=True, domain="doubao", err=err)
        _emit(out, action=action, silent=silent)
        return 1
    payload = body if isinstance(body, dict) else {}
    code = payload.get("code")
    data = payload.get("data") or {}
    if scode != 200 or code != 0:
        out = make_result(
            "ERROR",
            "业务失败 code=%s msg=%s(http=%s)" % (code, payload.get("msg")
                                                  or payload.get("message"), scode),
            needs_attention=True, domain="doubao", http=scode, biz_code=code)
        _emit(out, action=action, silent=silent)
        return 1
    amount = data.get("reward_amount")
    days = data.get("continuous_days")
    detail = "+%s额度(连签%s天)" % (amount if amount is not None else "?",
                                  days if days is not None else "?")
    if data.get("idempotent"):
        out = make_result("ALREADY", "今日已签到 %s" % detail, domain="doubao",
                          checkin_date=data.get("checkin_date"), reward_amount=amount,
                          continuous_days=days)
        _emit(out, action=action, silent=silent)
        return 0
    out = make_result("CLAIMED", "签到成功 %s" % detail, domain="doubao",
                      checkin_date=data.get("checkin_date"), reward_amount=amount,
                      continuous_days=days)
    _emit(out, action=action, silent=silent)
    return 0


def _status(api: DoubaoApi, config: DoubaoDomainConfig, *, action: str, silent: bool) -> int:
    """活动详情查询(只读): 活动是否在期 + 面板是否可用。"""
    scode, body, err = api.campaign_detail(config.campaign_code)
    payload = body if isinstance(body, dict) else {}
    code = payload.get("code")
    if scode == 200 and code == 0:
        out = make_result("OK", "活动在期: %s" % config.campaign_code,
                          domain="doubao", campaign_code=config.campaign_code)
        _emit(out, action=action, silent=silent)
        return 0
    if scode == 200 and code == 2:
        out = make_result("INACTIVE_ERROR",
                          "活动不在期或已换季(code=2 campaign not found): "
                          "请更新 config 的 campaign_code,见 NOTE-04",
                          needs_attention=True, domain="doubao",
                          campaign_code=config.campaign_code)
        _emit(out, action=action, silent=silent)
        return 1
    out = make_result("ERROR", "查询失败 http=%s code=%s err=%s" % (scode, code, err),
                      needs_attention=True, domain="doubao", http=scode)
    _emit(out, action=action, silent=silent)
    return 1


def _doctor(config: DoubaoDomainConfig, *, action: str, silent: bool) -> int:
    """离线体检: 平台/数据目录/Cookie 解密链路逐项检查(不联网)。"""
    report = cookies.doctor_report(_user_data_dir(config))
    ok = bool(report.get("session_ok"))
    out = make_result(
        "OK" if ok else "ERROR",
        "体检通过" if ok else "体检未通过: %s" % report.get("session_error", "见 checks 字段"),
        needs_attention=not ok, domain="doubao", checks=report)
    _emit(out, action=action, silent=silent)
    return 0 if ok else 1


def run_action(
    action: str, config: DoubaoDomainConfig, *, silent: bool = False,
    dry_run: bool = False, once: bool = False, command: str | None = None,
) -> int:
    """命令分发;结果按一行 JSON 契约输出,退出码 0/1/2,本函数保证不抛业务异常。

    ``command`` 是用户/调度器实际敲的 CLI 命令名(如 ``auto``),用于台账
    ``action`` 标识;缺省回落到内部 action 名。
    """
    label = command or action
    if action not in KNOWN_ACTIONS:
        emit_result(
            make_result("ERROR", "未知命令：%s（可用：%s）" % (action, " / ".join(KNOWN_ACTIONS)),
                        domain="doubao"),
            action=label, silent=silent, log_path=_log_path(),
            log_errors_without_silent=False)
        return 2
    if action == "doctor":
        return _doctor(config, action=label, silent=silent)
    try:
        api = _build_api(config, once=once)
    except AutoSignInKitError as exc:
        result = "NO_AUTH" if isinstance(exc, cookies.DoubaoCookieError) else "ERROR"
        out = make_result(result, str(exc), needs_attention=True, domain="doubao")
        _emit(out, action=label, silent=silent)
        return exc.exit_code
    if action == "claim":
        return _checkin(api, config, dry_run=dry_run, action=label, silent=silent)
    return _status(api, config, action=label, silent=silent)
