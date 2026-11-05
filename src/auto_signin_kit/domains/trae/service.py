"""Trae CN 每日签到: 状态查询 → 幂等领取 → 一行 JSON。

契约对齐 Trae CN 桌面端 ttnet 调用(2026-10-21 重新取证): status 先行,
``checked_in=true`` 直接回 ALREADY;claim 的 body 为 ``{"req_source": 1}``
且必须带 ``x-device-id`` 等设备头(缺了回业务码 9004)。claim 受理响应只有
``{"code": 0}`` 不回显 checked_in(2026-10-22 实测),签到确认以 status 复查为准。
写操作默认不重试,
唯一例外是 9074 活动限流——claim 服务端幂等,同轮内退避重试。
"""

from __future__ import annotations

import json
import time
from typing import Any

from auto_signin_kit.core.dto import RunOutcome
from auto_signin_kit.core.httpclient import Budget
from auto_signin_kit.core.kit.accounts import account_ref
from auto_signin_kit.core.kit.domain import DomainError, SigninDomain
from auto_signin_kit.core.kit.transport import transport_error_dto
from auto_signin_kit.domains.trae.api import (
    SENSITIVE,
    Api,
    build_api,
    parse_budget,
)
from auto_signin_kit.domains.trae.auth import NoAuthError, TraeAuthError


def _short(value: Any, limit: int = 160) -> str:
    """调试展示: 压扁成单行并截断。"""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return " ".join(text.split())[:limit]


def _body(payload: Any) -> dict:
    return payload if isinstance(payload, dict) else {}


def _transport_errors(code: int, payload: Any, err: str | None) -> RunOutcome | None:
    """HTTP/伪状态层统一映射;业务 code(1001 等)不在这里处理。"""
    return transport_error_dto(code, payload, err, auth_reports={
        401: "登录态被服务端拒绝（HTTP 401），请打开 Trae CN 桌面端重新登录",
    })


def _business_error(code: int, payload: dict) -> RunOutcome | None:
    """业务码层映射;0=成功,1001=认证被拒,9074=活动限流,其余如实上报。"""
    if code == 0:
        return None
    if code == 9074:
        return {"result": "RATE_LIMITED", "needs_attention": True, "code": code,
                "report": "活动参与人数过多被服务端限流（code=9074），已按退避重试仍失败，"
                          "等 schedule 定时任务到点自然补跑"}
    return {"result": "ERROR", "needs_attention": True, "code": code,
            "report": "业务失败 code=%s：%s" % (code, _short(payload.get("message")) or "无消息")}


def check_status(api: Api, budget: Budget) -> RunOutcome:
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


def claim_once(api: Api, budget: Budget, *, dry_run: bool = False) -> RunOutcome:
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
    # 9074 是活动期限流(1024 活动实测,2026-10-22 用 IDE 同款五件套设备头
    # 复刻请求仍 9074——确认是服务端排队而非请求问题): claim 服务端幂等
    # (NOTE-03),按"确认幂等的签到接口可重试"的项目约定在同轮内退避重试,
    # 否则一天几次触发全撞上限流就整日白跑。退避 30+60+120s≈210s,加请求
    # 时间贴着 240s 预算收尾;预算不足时提前收手,带限流结果交给下一次调度。
    business: dict | None = None
    body: dict = {}
    for backoff in (None, 30.0, 60.0, 120.0):
        if backoff is not None:
            if budget.remaining() < backoff:
                break  # 预算撑不起再等一轮: 带着限流结果收尾,交给下一次调度
            time.sleep(backoff)
        code, payload, err = api.claim(budget=budget)
        transport = _transport_errors(code, payload, err)
        if transport:
            return transport
        body = _body(payload)
        if code != 200:
            return {"result": "ERROR", "needs_attention": True,
                    "report": "领取失败：HTTP %s %s" % (code, _short(payload))}
        business = _business_error(body.get("code") or 0, body)
        if business is None or business.get("result") != "RATE_LIMITED":
            break
    if business:
        return business
    if body.get("checked_in") is True:  # 服务端若回显 checked_in,直接采信
        return _claimed(body.get("credits"), body.get("extra_credits"))
    # claim 受理成功但响应不回显 checked_in(2026-10-22 实测响应只有
    # {"code": 0, "message": "success"}),以 status 复查为准;状态有延迟,
    # 按 0/3/8s 复查三轮,仍未确认就如实报 ERROR,交人工或下轮调度核实。
    for wait in (0.0, 3.0, 8.0):
        if wait:
            if budget.remaining() < wait:
                break
            time.sleep(wait)
        confirm = check_status(api, budget)
        if confirm.get("checked_in") is True:
            return _claimed(confirm.get("credits"), confirm.get("extra_credits"))
    return {"result": "ERROR", "needs_attention": True,
            "report": "领取已发出（code=0）但三轮状态复查未确认签到，"
                      "稍后用 `trae status` 复查确认"}


def _claimed(credits: Any, extra: Any) -> RunOutcome:
    """CLAIMED 结果的统一形状(claim 回显与 status 复查两条路共用)。"""
    summary = "领取成功：今日 %s Credits" % ("" if credits is None else credits)
    if extra:
        summary += "（含活动附加 %s）" % extra
    return {"result": "CLAIMED", "needs_attention": False, "credits": credits,
            "extra_credits": extra or None, "report": summary}


def run_claim(api: Api, budget: Budget, *, dry_run: bool = False) -> tuple[RunOutcome, int]:
    """auto 主流程;退出码 0=成功或幂等,1=失败,2=不可用。"""
    out = claim_once(api, budget, dry_run=dry_run)
    if api.token_expires_at:
        out.setdefault("token_expires_at", api.token_expires_at)
    code = 0 if out.get("result") in ("CLAIMED", "ALREADY", "DRY_RUN") else 1
    return out, code


def run_status(api: Api, budget: Budget) -> tuple[RunOutcome, int]:
    """只查不领;结果 OK/AUTH_REJECTED/... 。"""
    out = check_status(api, budget)
    if api.token_expires_at:
        out.setdefault("token_expires_at", api.token_expires_at)
    code = 0 if out.get("result") == "OK" else 1
    return out, code


class TraeDomain(SigninDomain):
    """trae 域: 只实现四钩子,流程外壳(命令分发/异常映射/台账/退出码)由基类锁定。"""

    name = "trae"
    display = "Trae CN"
    sensitive = SENSITIVE

    def build(self, config: Any, *, once: bool = False, dry_run: bool = False,
              account: str = "") -> Api:
        """登录态 → Api;三种凭证失败按语义转 DomainError。

        ``account``(SPEC-12 P2): 具名账户按 AccountRef.auth_dir 解析登录态
        与设备头(凭据=设备同文件,多账户即多数据目录)。
        """
        del once  # 查询+领取共两次请求,预算内完成;参数位与 qoder 对齐
        ref = account_ref(config, account) if account else None
        try:
            api, info, staleness = build_api(config, auth_dir=ref.auth_dir if ref else None)
        except NoAuthError as exc:
            raise DomainError("NO_AUTH", str(exc), exit_code=2,
                              extra={"looked_in": exc.looked_in or None}) from exc
        except TraeAuthError as exc:
            raise DomainError("AUTH_ERROR", str(exc), exit_code=1,
                              extra={"looked_in": exc.looked_in or None}) from exc
        if staleness == "EXPIRED":
            raise DomainError(
                "AUTH_REJECTED", "登录态已过期，请打开 Trae CN 桌面端重新登录",
                exit_code=1,
                extra={"token_expires_at": info.get("expiredAt") if info else None})
        return api

    def claim(self, api: Api, config: Any, *, dry_run: bool = False,
              once: bool = False) -> tuple[dict, int]:
        """status 先行 → 未签则 claim → 复查确认(主流程见 run_claim)。"""
        budget, warning = self._budget(config)
        out, code = run_claim(api, budget, dry_run=dry_run)
        if warning:
            out.setdefault("config_warning", warning)
        return out, code

    def status(self, api: Api, config: Any) -> tuple[dict, int]:
        """只查今日签到状态,不领取。"""
        budget, _ = self._budget(config)
        return run_status(api, budget)

    def doctor(self, config: Any, *, account: str = "") -> tuple[dict, int]:
        """离线检查登录态可读性与有效期(指定账户时体检该账户的目录)。"""
        from auto_signin_kit.domains.trae.doctor import run_doctor

        ref = account_ref(config, account) if account else None
        if ref is not None and ref.auth_dir:
            return run_doctor(ref.auth_dir, skip_env=True)
        return run_doctor(config.data_dir)

    def _budget(self, config: Any) -> tuple[Budget, str | None]:
        value, warning = parse_budget(config)
        return Budget(value), warning


domain = TraeDomain()
#: 模块级入口(CLI 工厂与既有调用方引用): 即模板方法绑定。
run_action = domain.run_action
