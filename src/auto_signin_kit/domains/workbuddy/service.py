"""workbuddy 域业务编排: 命令分发 / 时间预算 / 签到+成长中心的一轮日常。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from auto_signin_kit.core.httpclient import Budget
from auto_signin_kit.core.logging import result_log_path
from auto_signin_kit.core.report import emit_result, make_result
from auto_signin_kit.domains.workbuddy import checkin, rewards
from auto_signin_kit.domains.workbuddy.credentials import find_auth_file, load_session_retry
from auto_signin_kit.domains.workbuddy.extras import run_growth
from auto_signin_kit.domains.workbuddy.fields import env_flag
from auto_signin_kit.domains.workbuddy.net import Net
from auto_signin_kit.domains.workbuddy.schema import DEFAULT_ENDPOINT, WorkbuddyDomainConfig
from auto_signin_kit.domains.workbuddy.session import (
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


def log_path() -> Path:
    """silent 模式日志路径;WORKBUDDY_SIGNIN_LOG 覆盖,默认平台日志目录的 signin.log。"""
    return result_log_path("workbuddy", env_var="WORKBUDDY_SIGNIN_LOG", filename="signin.log")


def parse_budget(
    config: WorkbuddyDomainConfig, *, poll: bool
) -> tuple[float, str | None]:
    """解析预算: 环境变量优先、配置兜底;非法/越界夹回安全区间,绝不抛异常。"""
    default, maximum = (POLL_BUDGET, POLL_MAX_BUDGET) if poll else (DEFAULT_BUDGET, MAX_BUDGET)
    raw_str = os.environ.get("WORKBUDDY_BUDGET_SECONDS")
    raw = config.budget_seconds
    warning: str | None = None
    if raw_str:
        try:
            raw = float(raw_str)
            if raw <= 0:
                # "0" 是非空字符串,靠 not 兜不住;且 0 会让每个请求都直接放弃。
                warning = (
                    "WORKBUDDY_BUDGET_SECONDS=%s 必须为正数，已回落 %s 秒" % (raw_str, int(default))
                )
                raw = default
        except (TypeError, ValueError):
            return (
                default,
                "WORKBUDDY_BUDGET_SECONDS=%r 不是数字，已回落 %s 秒" % (raw_str, int(default)),
            )
    if raw is None or raw <= 0:
        raw = default
    if raw > maximum:
        return (
            maximum,
            "WORKBUDDY_BUDGET_SECONDS=%s 超过本命令上限 %s 秒"
            "（须小于该定时任务的 ExecutionTimeLimit）" % (raw, int(maximum)),
        )
    return raw, warning


def load_credentials() -> tuple[dict | None, dict | None]:
    """定位/读取/校验凭据;返回 (session, 失败结果)。两者必有其一为 None。"""
    auth_file, looked_in = find_auth_file()
    if auth_file is None or not auth_file.exists():
        override = os.environ.get("WORKBUDDY_AUTH_FILE")
        if override:
            report = "WORKBUDDY_AUTH_FILE 指向的文件不存在：%s" % override
        else:
            report = (
                "未找到 WorkBuddy 登录凭据。请先在本机登录 WorkBuddy 桌面端；"
                "或设置环境变量 WORKBUDDY_AUTH_FILE 指向 workbuddy-desktop.info。"
            )
        return None, {"result": "NO_AUTH", "report": report, "looked_in": looked_in,
                      "needs_attention": True}
    try:
        return load_session_retry(auth_file), None
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


def run_doctor(session: dict, budget: Budget) -> dict:
    """离线体检: 校验凭据格式与解密运行时能力;不解密 token、不联网。"""
    _validate_session(session)
    kind = _token_format(session["auth"]["accessToken"])
    from auto_signin_kit.domains.workbuddy import runtime as runtime_mod

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


def run_daily(net: Net) -> tuple[int, dict, bool]:
    """一轮完整日常: 签到 → 成长中心;返回 (退出码, 结果, 是否空跑)。

    轮询也走它而不是只跑成长中心: 签到原本一天一次,撞上关机/断网当天断签;
    轮询先查状态、未签才补(幂等),等于一天七次机会。成长中心的问题不吞掉
    签到已成功的事实,但会让本轮退出码非零、needs_attention 置位。
    """
    code, out = checkin.run_auto(net)
    if out.get("result") in ("NETWORK", "TIMEOUT"):
        out["growth"] = "网络不可达或时间预算耗尽，成长中心跳过"
        out["growth_result"] = out["result"]
        return code, out, False
    if out.get("result") in ("NO_SESSION", "AUTH_REJECTED", "FORBIDDEN"):
        out["growth"] = "认证或权限检查未通过，成长中心跳过"
        out["growth_result"] = out["result"]
        return code, out, False
    try:
        gcode, gout = run_growth(net, net.budget_remaining)
    except AuthError as e:
        gcode, gout = 1, e.output()
    except Exception as e:  # noqa: BLE001
        gcode, gout = 1, {"result": "ERROR", "needs_attention": True,
                          "report": "成长中心异常（%s: %s）" % (type(e).__name__, e)}
    out["growth"] = gout.get("report")
    out["growth_result"] = gout.get("result")
    if gout.get("credits_gained") or gcode != 0:
        out["report"] += "；" + gout["report"]
    if gcode != 0 and code == 0:
        code = gcode
    out["needs_attention"] = code != 0
    quiet = out.get("result") in ("ALREADY", "INACTIVE") and bool(gout.get("idle"))
    return code, out, quiet


def run_action(action: str, config: WorkbuddyDomainConfig, *, silent: bool = False) -> int:
    """命令分发;结果一律按一行 JSON 契约输出,本函数保证不抛业务异常。"""
    if action not in KNOWN_ACTIONS:
        emit_result(
            make_result("ERROR", "未知命令：%s（可用：%s）" % (action, " / ".join(KNOWN_ACTIONS))),
            silent=silent, log_path=log_path(),
        )
        return 2
    poll = action in POLL_ACTIONS
    budget_value, warning = parse_budget(config, poll=poll)
    budget = Budget(budget_value)

    session, failure = load_credentials()
    if failure is not None:
        emit_result(failure, silent=silent, log_path=log_path())
        return 2

    if action == "doctor":
        try:
            out = run_doctor(session, budget)
        except AuthError as e:
            emit_result(e.output(), silent=silent, log_path=log_path())
            return 1
        emit_result(out, silent=silent, log_path=log_path())
        return 0

    try:
        resolved = resolve_session(session, budget)
        headers = build_headers(resolved)
    except AuthError as e:
        emit_result(e.output(), silent=silent, log_path=log_path())
        return 1
    endpoint = ((resolved.get("auth") or {}).get("endpoint") or config.endpoint
                or DEFAULT_ENDPOINT).rstrip("/")
    net = Net(endpoint, headers, budget)

    if action in ("auto", "silent") or poll:
        code, out, quiet = run_daily(net)
        if poll:
            out["trigger"] = "poll"
            if quiet and not env_flag("WORKBUDDY_GROWTH_LOG_EMPTY"):
                return code  # 空跑不落盘,免得有价值的记录被淹没
        if warning:
            out.setdefault("config_warning", warning)
        emit_result(out, silent=silent or (action in POLL_ACTIONS or action == "silent"),
                    log_path=log_path(), sensitive=SENSITIVE)
        return code

    if action == "active":
        try:
            out = rewards.run_active(net)
        except AuthError as e:
            out = e.output()
        if warning:
            out.setdefault("config_warning", warning)
        emit_result(out, silent=silent, log_path=log_path(), sensitive=SENSITIVE)
        # NO_ACTIVITY 不是脚本失败,但它需要人去补一次对话,所以也走非零退出码。
        return 0 if out.get("result") == "ACTIVE" else 1

    if action == "growth":
        try:
            gcode, gout = run_growth(net, net.budget_remaining)
        except AuthError as e:
            gcode, gout = 1, e.output()
        gout["needs_attention"] = gcode != 0
        if warning:
            gout.setdefault("config_warning", warning)
        emit_result(gout, silent=silent, log_path=log_path(), sensitive=SENSITIVE)
        return gcode

    return run_debug(action, net)


def run_debug(action: str, net: Net) -> int:
    """status/claim/all: 输出原始返回,调试用;状态查询失败时不继续领取。"""
    if action in ("status", "all"):
        scode, sbody = net.post(checkin.STATUS_SUFFIX, retry=True)
        if scode in (401, 403):
            emit_result(dict(checkin.auth_failure_dto(scode), step="status"),
                        sensitive=SENSITIVE, log_path=log_path())
            return 1
        emit_result(make_result("OK", "已查询签到状态", step="status", http=scode, body=sbody,
                                needs_attention=not 200 <= scode < 300),
                    sensitive=SENSITIVE, log_path=log_path())
        if not 200 <= scode < 300:
            return 1
    if action in ("claim", "all"):
        ccode, cbody = net.post(checkin.CLAIM_SUFFIX, retry=True)
        if ccode in (401, 403):
            emit_result(dict(checkin.auth_failure_dto(ccode), step="claim"),
                        sensitive=SENSITIVE, log_path=log_path())
            return 1
        success = 200 <= ccode < 300 or (ccode == 400 and checkin.is_already_checked_in(cbody))
        emit_result(make_result("OK", "已请求领取签到", step="claim", http=ccode, body=cbody,
                                needs_attention=not success),
                    sensitive=SENSITIVE, log_path=log_path())
        if not success:
            return 1
    return 0
