"""doubao 域业务逻辑: 签到领取/活动查询/体检。

**禁止 import typer**;交互走 ``core.prompt`` 注入,输出走 ``core.report``。
接口契约见 ``api.py`` 模块 docstring 与 docs/notes/NOTE-04。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.httpclient import NO_NETWORK, Budget
from auto_signin_kit.core.logging import log_debug, result_log_path
from auto_signin_kit.core.report import emit_result, make_result
from auto_signin_kit.core.store import done_today
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


def _sweep_rewards(api: DoubaoApi, config: DoubaoDomainConfig) -> dict[str, Any]:
    """清扫活动任务奖励: 只领 ``task_status=2``(已完成待领取)的项。

    体验任务(生成文档/表格/PPT 等)须先在产品里真实完成对应操作才会变为
    可领取(status=2),清扫对未完成项不做任何事——不能凭空硬领。
    """
    scode, body, err = api.campaign_detail(config.campaign_code)
    payload = body if isinstance(body, dict) else {}
    if scode != 200 or payload.get("code") != 0:
        return {"error": "活动详情查询失败(http=%s code=%s)" % (scode, payload.get("code"))}
    data = payload.get("data") or {}
    items = (data.get("tasks") or []) + (data.get("milestones") or [])
    claimable = [t for t in items if t.get("task_status") == 2]
    claimed: list[dict[str, Any]] = []
    failures: list[str] = []
    for task in claimable:
        code = task.get("campaign_code") or ""
        title = task.get("title") or code
        csc, cbody, cerr = api.claim_task(code, config.campaign_code)
        cpayload = cbody if isinstance(cbody, dict) else {}
        cdata = cpayload.get("data") or {}
        biz = cdata.get("biz_error") or {}
        if csc == 200 and cpayload.get("code") == 0 and not biz:
            rewards = task.get("reward_summaries") or [{}]
            claimed.append({"code": code, "title": title,
                            "amount": rewards[0].get("reward_amount")})
        else:
            failures.append("%s: %s" % (title, biz.get("message") or cerr or csc))
    pending = sum(1 for t in items if t.get("task_status") == 1)
    return {"claimed": claimed, "failures": failures, "pending": pending}


def _checkin(api: DoubaoApi, config: DoubaoDomainConfig, *, dry_run: bool,
             action: str, silent: bool) -> int:
    """签到 + 任务奖励清扫;幂等接口,重复运行返回已签状态。"""
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
        # 网关码映射: 710012* = 登录态失效(NO_AUTH,指引重开客户端恢复会话),
        # 其余按 ERROR。2026-11-03 排查实录: 服务端对过期会话在签到接口上
        # 会回误导性的 710010702"系统错误",710012001 才是明确过期;
        # 两值在台账词表(core.report.RESULT_LABELS)都有标签。
        no_auth = str(code or "").startswith("710012")
        out = make_result(
            "NO_AUTH" if no_auth else "ERROR",
            ("登录已过期(code=%s): 打开或重启豆包客户端恢复会话后重试"
             % code) if no_auth else
            "业务失败 code=%s msg=%s(http=%s)" % (code, payload.get("msg")
                                                  or payload.get("message"), scode),
            needs_attention=True, domain="doubao", http=scode, biz_code=code)
        _emit(out, action=action, silent=silent)
        return 1

    # 签到成功/已签后,清扫活动任务奖励(详情查询失败不掩盖签到结果)
    sweep: dict[str, Any] = _sweep_rewards(api, config)
    parts: list[str] = []
    amount = data.get("reward_amount")
    days = data.get("continuous_days")
    if data.get("idempotent"):
        parts.append("今日已签到 +%s额度(连签%s天)" % (amount if amount is not None else "?",
                                                    days if days is not None else "?"))
        result = "ALREADY"
    else:
        parts.append("签到成功 +%s额度(连签%s天)" % (amount if amount is not None else "?",
                                                    days if days is not None else "?"))
        result = "CLAIMED"
    claimed = sweep.get("claimed") or []
    task_amount = sum(c["amount"] or 0 for c in claimed)
    if claimed:
        result = "CLAIMED"
        parts.append("任务奖励 %d 项 +%s额度(%s)" % (
            len(claimed), task_amount, "、".join(c["title"] for c in claimed)))
    if sweep.get("pending"):
        parts.append("%d 项任务未完成(完成后面板可领)" % sweep["pending"])
    if sweep.get("failures"):
        parts.append("领取失败: %s" % "; ".join(sweep["failures"]))
    if sweep.get("error"):
        parts.append("[%s]" % sweep["error"])
    out = make_result(result, "; ".join(parts), domain="doubao",
                      checkin_date=data.get("checkin_date"), reward_amount=amount,
                      continuous_days=days, task_rewards=claimed,
                      task_pending=sweep.get("pending", 0))
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
    # 调度兜底触发(高密度时间点): 当天已成功签到就不再执行,也不重复落库
    if silent and action == "claim" and done_today("doubao"):
        log_debug("doubao: 今日已成功签到,调度触发跳过(不执行、不落库)")
        return 0
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
