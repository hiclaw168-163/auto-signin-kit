"""workbuddy 域业务编排: 命令分发 / 时间预算;签到→成长中心的一轮日常见 daily.py。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from auto_signin_kit.core.dto import RunOutcome
from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.httpclient import Budget
from auto_signin_kit.core.kit.accounts import (
    account_enabled,
    account_ref,
    resolve_account,
)
from auto_signin_kit.core.kit.actions import DEFAULT_ACCOUNT, skip_done_today, unknown_action_report
from auto_signin_kit.core.kit.budget import parse_budget as core_parse_budget
from auto_signin_kit.core.logging import log_debug, result_log_path
from auto_signin_kit.core.report import emit_result, make_result
from auto_signin_kit.domains.client_signin.apps.workbuddy_cn import checkin, daily, rewards
from auto_signin_kit.domains.client_signin.apps.workbuddy_cn.credentials import (
    find_auth_file,
    load_session_retry,
)
from auto_signin_kit.domains.client_signin.apps.workbuddy_cn.extras import run_growth
from auto_signin_kit.domains.client_signin.apps.workbuddy_cn.net import Net
from auto_signin_kit.domains.client_signin.apps.workbuddy_cn.schema import (
    DEFAULT_ENDPOINT,
    WorkbuddyDomainConfig,
)
from auto_signin_kit.domains.client_signin.apps.workbuddy_cn.session import (
    SENSITIVE,
    AuthError,
    _token_format,
    _validate_session,
    build_headers,
    resolve_session,
    run_helper,
)

#: 轮询任务 PT5M: 上限 240 = 300 - 60 收尾余量;签到任务 PT10M 对应 540。
POLL_ACTIONS = ("poll", "silent-poll", "silent-growth")
DEFAULT_BUDGET = 420.0
MAX_BUDGET = 540.0
POLL_BUDGET = 180.0
POLL_MAX_BUDGET = 240.0

KNOWN_ACTIONS = (
    "auto", "silent", "growth", "poll", "silent-poll", "silent-growth",
    "status", "claim", "all", "doctor", "active",
)

#: 静默调度时,当天已成功签到即可整体跳过的动作(**纯签到**主入口与其兜底);
#: poll/silent-poll/silent-growth 不跳——它们带成长中心(旅行礼物/任务领奖),
#: 是独立于签到的收益源,"签了到"不等于"旅行礼物已领"(2026-11-04 猫猫旅行
#: 积分漏领的根因);签到部分在 run_daily 里自查幂等,不需要外层跳过。
#: active(活跃保底)/growth(成长查询)/status/doctor 各有独立职责,也不跳。
SKIP_WHEN_DONE = ("auto", "silent", "claim", "all")


def log_path() -> Path:
    """silent 模式日志路径;WORKBUDDY_SIGNIN_LOG 覆盖,默认项目日志目录的 workbuddy.log。"""
    return result_log_path("workbuddy_cn", env_var="WORKBUDDY_SIGNIN_LOG")


def parse_budget(
    config: WorkbuddyDomainConfig, *, poll: bool
) -> tuple[float, str | None]:
    """解析预算: 环境变量优先、配置兜底;非法/越界夹回安全区间,绝不抛异常。"""
    default, maximum = (POLL_BUDGET, POLL_MAX_BUDGET) if poll else (DEFAULT_BUDGET, MAX_BUDGET)
    return core_parse_budget(
        config.budget_seconds, default, maximum, env_var="WORKBUDDY_BUDGET_SECONDS")


def load_credentials(*, auth_file: str | None = None) -> tuple[dict | None, dict | None]:
    """定位/读取/校验凭据;返回 (session, 失败结果)。两者必有其一为 None。

    ``auth_file``(SPEC-12 P2): 具名账户的显式凭据路径;缺省走环境变量/
    平台扫描,行为不变。
    """
    auth_path, looked_in = find_auth_file(auth_file=auth_file)
    if auth_path is None or not auth_path.exists():
        if auth_file:
            report = "账户凭据文件不存在：%s" % auth_file
        elif os.environ.get("WORKBUDDY_AUTH_FILE"):
            report = "WORKBUDDY_AUTH_FILE 指向的文件不存在：%s" % os.environ["WORKBUDDY_AUTH_FILE"]
        else:
            report = (
                "未找到 WorkBuddy 登录凭据。请先在本机登录 WorkBuddy 桌面端；"
                "或设置环境变量 WORKBUDDY_AUTH_FILE 指向 workbuddy-desktop.info。"
            )
        return None, {"result": "NO_AUTH", "report": report, "looked_in": looked_in,
                      "needs_attention": True}
    try:
        return load_session_retry(auth_path), None
    except json.JSONDecodeError as e:
        return None, {"result": "ERROR",
                      "report": "登录凭据文件不是合法 JSON（%s），请重新登录客户端" % e,
                      "needs_attention": True}
    except ValueError as e:
        detail = "%s: %s" % (type(e).__name__, e)
        return None, {"result": "ERROR",
                      "report": "登录凭据文件内容损坏（%s），请重新登录客户端" % detail,
                      "needs_attention": True}
    except OSError as e:
        return None, {"result": "ERROR",
                      "report": "读取登录凭据失败（%s），请重新登录客户端" % e,
                      "needs_attention": True}


def run_doctor(session: dict, budget: Budget) -> RunOutcome:
    """离线体检: 校验凭据格式与解密运行时能力;不解密 token、不联网。"""
    _validate_session(session)
    kind = _token_format(session["auth"]["accessToken"])
    from auto_signin_kit.domains.client_signin.apps.workbuddy_cn import runtime as runtime_mod

    out: dict[str, Any] = {
        "result": "AUTH_READY", "credential_format": kind, "needs_attention": False,
        "report": "本地凭据格式有效；未验证服务端登录状态", "online_checked": False,
    }
    if kind == "sym-v1":
        exe = runtime_mod.find_workbuddy_runtime(budget)
        probe = run_helper(exe, {"operation": "probe"}, budget)
        if (
            set(probe) != {"version", "ok", "electron"}
            or not isinstance(probe.get("electron"), str)
        ):
            raise AuthError("HELPER_PROTOCOL")
        out.update(
            runtime=exe, electron_version=probe["electron"],
            report="已识别加密凭据，运行时具备解密能力；未提取密钥、未验证解密或服务端登录状态",
        )
    return out


def run_action(action: str, config: WorkbuddyDomainConfig, *, silent: bool = False,
               account: str = DEFAULT_ACCOUNT) -> int:
    """命令分发;结果一律按一行 JSON 契约输出,本函数保证不抛业务异常。

    ``account``(SPEC-12): 未声明直接报错;停用账户静默调度跳过,手动跑放行;
    全程透传进台账信封(emit_result 的 account=)。会话身份 dict 已改名 acct。
    """
    try:
        account = resolve_account(config, account)
    except AutoSignInKitError as exc:
        emit_result(make_result("FAILED", str(exc), domain="workbuddy_cn"),
                    action=action, silent=silent, log_path=log_path())
        return exc.exit_code
    # 停用账户只在静默(调度)路径跳过;显式 --account 手动跑永远放行
    if silent and not account_enabled(config, account):
        log_debug("workbuddy/%s: 账户已停用,调度触发跳过(不执行、不落库)" % account)
        return 0
    if action not in KNOWN_ACTIONS:
        emit_result(
            make_result("ERROR", unknown_action_report(action, KNOWN_ACTIONS),
                        domain="workbuddy_cn"),
            action=action, silent=silent, log_path=log_path(), account=account,
        )
        return 2
    # 调度兜底触发(高密度时间点): 当天该账户已成功签到就不再执行,也不重复落库
    if skip_done_today("workbuddy_cn", action, silent=silent, skip_when_done=SKIP_WHEN_DONE,
                       account=account):
        return 0
    poll = action in POLL_ACTIONS
    budget_value, warning = parse_budget(config, poll=poll)
    budget = Budget(budget_value)

    # 凭据 profile(SPEC-12 P2): 具名账户用 AccountRef.auth_file 显式路径,
    # default/未声明沿用环境变量与平台扫描链路
    ref = account_ref(config, account)
    session, failure = load_credentials(auth_file=ref.auth_file if ref else None)
    if failure is not None:
        emit_result(dict(failure, account=account), domain="workbuddy_cn", action=action,
                    silent=silent, log_path=log_path())
        return 2

    if action == "doctor":
        try:
            out = run_doctor(session, budget)
        except AuthError as e:
            emit_result(e.output(), domain="workbuddy_cn", action=action, silent=silent,
                        log_path=log_path(), account=account)
            return 1
        emit_result(out, domain="workbuddy_cn", action=action, silent=silent,
                    log_path=log_path(), account=account)
        return 0

    try:
        resolved = resolve_session(session, budget)
        headers = build_headers(resolved)
    except AuthError as e:
        emit_result(e.output(), domain="workbuddy_cn", action=action, silent=silent,
                    log_path=log_path(), account=account)
        return 1
    endpoint = ((resolved.get("auth") or {}).get("endpoint") or config.endpoint
                or DEFAULT_ENDPOINT).rstrip("/")
    acct = resolved.get("account") or {}
    identity = {
        "uid": str(acct.get("uid") or ""),
        "nickname": str(acct.get("nickname") or ""),
        "enterprise": str(acct.get("enterpriseId") or ""),
    }
    net = Net(endpoint, headers, budget, identity=identity)

    if action in ("auto", "silent") or poll:
        code, out = daily.run_daily(net, taskbot=config.taskbot)
        if poll:
            out["trigger"] = "poll"
        if warning:
            out.setdefault("config_warning", warning)
        emit_result(out, domain="workbuddy_cn", action=action,
                    silent=silent or (action in POLL_ACTIONS or action == "silent"),
                    log_path=log_path(), sensitive=SENSITIVE, account=account)
        return code

    if action == "active":
        try:
            out = rewards.run_active(net)  # 检测 + 自动补对话 + 复查,链在 rewards.run_active
        except AuthError as e:
            out = e.output()
        if warning:
            out.setdefault("config_warning", warning)
        emit_result(out, domain="workbuddy_cn", action=action, silent=silent,
                    log_path=log_path(), sensitive=SENSITIVE, account=account)
        # 只有补完对话仍无活跃记录(或链路失败)才非零退出;ACTIVE 为 0。
        return 0 if out.get("result") == "ACTIVE" else 1

    if action == "growth":
        try:
            gcode, gout = run_growth(net, net.budget_remaining, taskbot=config.taskbot)
        except AuthError as e:
            gcode, gout = 1, e.output()
        gout["needs_attention"] = gcode != 0
        if warning:
            gout.setdefault("config_warning", warning)
        emit_result(gout, domain="workbuddy_cn", action=action, silent=silent,
                    log_path=log_path(), sensitive=SENSITIVE, account=account)
        return gcode

    return run_debug(action, net, account=account)


def run_debug(action: str, net: Net, *, account: str = DEFAULT_ACCOUNT) -> int:
    """status/claim/all: 输出原始返回,调试用;状态查询失败时不继续领取。"""
    if action in ("status", "all"):
        scode, sbody = net.post(checkin.STATUS_SUFFIX, retry=True)
        if scode in (401, 403):
            emit_result(dict(checkin.auth_failure_dto(scode), step="status", domain="workbuddy_cn"),
                        action=action, sensitive=SENSITIVE, log_path=log_path(),
                        account=account)
            return 1
        emit_result(make_result("OK", "已查询签到状态", step="status", http=scode, body=sbody,
                                needs_attention=not 200 <= scode < 300,
                                domain="workbuddy_cn"),
                    action=action, sensitive=SENSITIVE, log_path=log_path(),
                    account=account)
        if not 200 <= scode < 300:
            return 1
    if action in ("claim", "all"):
        ccode, cbody = net.post(checkin.CLAIM_SUFFIX, retry=True)
        if ccode in (401, 403):
            emit_result(dict(checkin.auth_failure_dto(ccode), step="claim", domain="workbuddy_cn"),
                        action=action, sensitive=SENSITIVE, log_path=log_path(),
                        account=account)
            return 1
        success = 200 <= ccode < 300 or (ccode == 400 and checkin.is_already_checked_in(cbody))
        emit_result(make_result("OK", "已请求领取签到", step="claim", http=ccode, body=cbody,
                                needs_attention=not success,
                                domain="workbuddy_cn"),
                    action=action, sensitive=SENSITIVE, log_path=log_path(),
                    account=account)
        if not success:
            return 1
    return 0
