"""web_signin 执行器: 把 AppDeclaration 的请求声明解释成「查询 → 幂等领取 → 确认」。

流程外壳对齐 trae 域 claim_once 的成熟设计(NOTE-03): status 先行,未签才
claim;claim 是写操作默认不重试,唯一例外是声明的限流业务码(retry_on)——
确认幂等的签到接口按退避 30+60+120s 重试,预算不足提前收手;领后复查
(confirm_by_status)覆盖"受理成功但响应不回显结果"的服务端(trae 实测)。
"""

from __future__ import annotations

import time
from typing import Any

from auto_signin_kit.core.dto import RunOutcome
from auto_signin_kit.core.httpclient import Budget, RetryPolicy, call
from auto_signin_kit.core.kit.transport import short_text, transport_error_dto
from auto_signin_kit.domains.web_signin.authkit import Credential, dig
from auto_signin_kit.domains.web_signin.schema import (
    AppDeclaration,
    ClaimDecl,
    RequestDecl,
    StatusDecl,
)

#: 网络类/5xx 退避节奏(对齐 trae 域): 查询可重试,领取默认不重试。
RETRY_POLICY = RetryPolicy(network_delays=(5, 15, 30), server_delays=(5, 15, 30))
#: 限流业务码的同轮退避表;30+60+120≈210s,贴着 240s 预算收尾。
CLAIM_BACKOFFS = (30.0, 60.0, 120.0)
#: 领后复查的等待节奏(秒);状态有延迟,三轮未确认如实报 ERROR。
CONFIRM_WAITS = (0.0, 3.0, 8.0)


class Executor:
    """绑定声明与凭据的请求器;所有请求经 core.httpclient(预算/重试统一)。"""

    def __init__(self, decl: AppDeclaration, cred: Credential, budget: Budget) -> None:
        """绑定一份声明、一份凭据与时间预算;请求头合并顺序在此收口。"""
        self.decl = decl
        self.cred = cred
        self.budget = budget

    def headers_for(self, req: RequestDecl) -> dict[str, str]:
        """头合并顺序: 全局静态 < 凭据头 < 动作静态(后写覆盖,动作级最具体)。"""
        headers = {"Accept": "application/json"}
        headers.update(self.decl.headers)
        headers.update(self.cred.headers)
        headers.update(req.headers)
        if req.body and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
        return headers

    def request(self, req: RequestDecl, *, retry: bool = True) -> tuple[int, Any, str | None]:
        """发一次声明请求;返回 (HTTP 状态/伪状态, JSON body, err)。"""
        return call(
            self.decl.base_url.rstrip("/") + req.path,
            headers=self.headers_for(req),
            method=req.method,
            payload=req.body or None,
            budget=self.budget,
            policy=RETRY_POLICY,
            timeout=self.decl.timeout,
            retry=retry,
        )

    # ------------------------------------------------------------ 结果映射

    def transport(self, code: int, payload: Any, err: str | None) -> RunOutcome | None:
        """HTTP 401/403/断网/预算耗尽 → 统一 DTO;业务码不在这里处理。"""
        return transport_error_dto(code, payload, err, auth_reports={
            401: "登录态被服务端拒绝（HTTP 401），请打开客户端重新登录",
        })

    def business(self, payload: Any) -> RunOutcome | None:
        """业务码层映射: code_ok 视为成功(返回 None),其余按声明/默认 ERROR。"""
        if not isinstance(payload, dict):
            return None
        code = dig(payload, self.decl.code_path)
        if code == self.decl.code_ok:
            return None
        label = self.decl.business_codes.get(code if isinstance(code, int) else -1, "ERROR")
        return {"result": label, "needs_attention": True, "code": code,
                "report": "业务失败 code=%s：%s" % (code,
                                                   short_text(payload.get("message")) or "无消息")}


def _extract_mapped(payload: Any, mapping: dict[str, str]) -> dict[str, Any]:
    """按 mapping 抽取统一字段(只带声明的键,缺路径不产键)。"""
    out: dict[str, Any] = {}
    for key, dotted in mapping.items():
        value = dig(payload, dotted)
        if value is not None:
            out[key] = value
    return out


def _amount_text(ex: Executor, amount: Any) -> str:
    return "" if amount is None else str(amount)


def check_status(ex: Executor) -> RunOutcome:
    """查询并规整签到状态(不做修改);result OK/传输层/业务层标签。"""
    status: StatusDecl | None = ex.decl.status
    if status is None:  # 纯 claim 型应用: 无状态可查
        return {"result": "OK", "needs_attention": False, "checked_in": None,
                "report": "该应用未声明 status,跳过查询直接领取"}
    code, payload, err = ex.request(status)
    transport = ex.transport(code, payload, err)
    if transport:
        return transport
    if code != 200:
        return {"result": "ERROR", "needs_attention": True,
                "report": "查询失败：HTTP %s %s" % (code, short_text(payload))}
    business = ex.business(payload)
    if business:
        return business
    mapped = _extract_mapped(payload, status.mapping)
    checked_in = mapped.get("checked_in")
    if checked_in is None:  # 没映射 checked_in 视为"无法判定",按未签处理
        return {"result": "OK", "needs_attention": False, "checked_in": None,
                "report": "状态查询成功但未映射 checked_in，按未签处理"}
    report = ("今日已签到（累计 %s Credits）" % mapped.get("credits")
              if checked_in is True else "今日尚未签到")
    return {"result": "OK", "needs_attention": False, **mapped, "report": report}


def _claim_amount(ex: Executor, payload: Any) -> Any:
    """领取数额: 优先 claim 响应的 amount_path,缺省回落 status 映射的 credits。"""
    path = ex.decl.claim.amount_path
    if path and isinstance(payload, dict):
        value = dig(payload, path)
        if value is not None:
            return value
    status = ex.decl.status
    if status is not None and isinstance(payload, dict):
        dotted = status.mapping.get("credits")
        if dotted:
            return dig(payload, dotted)
    return None


def _claimed(ex: Executor, payload: Any, *, amount: Any = None) -> RunOutcome:
    """CLAIMED 结果的统一形状(claim 回显与 status 复查两条路共用)。"""
    value = amount if amount is not None else _claim_amount(ex, payload)
    summary = "领取成功" + ("：今日 %s Credits" % _amount_text(ex, value) if value else "")
    return {"result": "CLAIMED", "needs_attention": False,
            "credits": value, "report": summary}


def _confirm_by_status(ex: Executor) -> RunOutcome | None:
    """领后复查三轮;确认已签返回 CLAIMED,未确认返回 None(交上层报错)。"""
    for wait in CONFIRM_WAITS:
        if wait and ex.budget.remaining() < wait:
            break
        if wait:
            time.sleep(wait)
        confirm = check_status(ex)
        if confirm.get("checked_in") is True:
            return _claimed(ex, None, amount=confirm.get("credits"))
    return None


def _claim_request(ex: Executor) -> tuple[int, Any, str | None]:
    """发领取请求;HTTP 层不可重试(写操作防重复),失败直接返回。"""
    return ex.request(ex.decl.claim, retry=False)


def _claim_with_retry(ex: Executor) -> tuple[RunOutcome | None, dict, int, Any, str | None]:
    """领取(含限流退避重试);返回 (业务层结果或None, 受理payload, http码, body, err)。"""
    for index, backoff in enumerate((None, *CLAIM_BACKOFFS)):
        if backoff is not None:
            if ex.budget.remaining() < backoff:
                break  # 预算撑不起再等一轮: 带着限流结果收尾,交给下一次调度
            time.sleep(backoff)
        code, payload, err = _claim_request(ex)
        transport = ex.transport(code, payload, err)
        if transport:
            return transport, {}, code, payload, err
        if code != 200:
            return {"result": "ERROR", "needs_attention": True,
                    "report": "领取失败：HTTP %s %s" % (code, short_text(payload))
                    }, {}, code, payload, err
        business = ex.business(payload)
        if business is None:
            return None, payload, code, payload, err
        if business.get("code") not in ex.decl.claim.retry_on or index == len(CLAIM_BACKOFFS):
            business.setdefault("report", business.get("report") or "领取失败")
            if business.get("code") in ex.decl.claim.retry_on:
                business["report"] = (
                    "命中限流 code=%s，已按退避重试仍失败，等 schedule 定时任务到点自然补跑"
                    % business["code"])
            return business, {}, code, payload, err
    return {"result": "RATE_LIMITED", "needs_attention": True,
            "report": "命中限流且时间预算耗尽，等 schedule 定时任务到点自然补跑"}, {}, 0, {}, None


def claim_once(ex: Executor, *, dry_run: bool = False) -> RunOutcome:
    """status → (未签时)claim → 复查确认。结果: CLAIMED/ALREADY/DRY_RUN/...。"""
    probe = check_status(ex)
    if probe.get("result") != "OK":
        return probe
    claim: ClaimDecl = ex.decl.claim
    if dry_run:
        if probe.get("checked_in") is True:
            return {"result": "ALREADY", "needs_attention": False,
                    "credits": probe.get("credits"),
                    "report": "[dry-run] 今日已签到（%s Credits）"
                              % _amount_text(ex, probe.get("credits"))}
        return {"result": "DRY_RUN", "needs_attention": False,
                "report": "[dry-run] 将发起领取请求 %s %s%s"
                          % (claim.method, claim.path,
                             "（今日未签到,可领取）" if probe.get("checked_in") is False else "")}
    if probe.get("checked_in") is True:
        return {"result": "ALREADY", "needs_attention": False, "credits": probe.get("credits"),
                "report": "今日已签到（%s Credits）" % _amount_text(ex, probe.get("credits"))}

    business, payload, _code, _body, _err = _claim_with_retry(ex)
    if business:
        return business
    if claim.checked_in_path and dig(payload, claim.checked_in_path) is True:
        return _claimed(ex, payload)  # 服务端回显 checked_in,直接采信
    if claim.confirm_by_status:
        confirmed = _confirm_by_status(ex)
        if confirmed:
            return confirmed
        return {"result": "ERROR", "needs_attention": True,
                "report": "领取已发出但复查未确认签到，稍后用 status 复查确认"}
    return _claimed(ex, payload)


def run_claim(ex: Executor, *, dry_run: bool = False) -> tuple[RunOutcome, int]:
    """auto 主流程;退出码 0=成功或幂等,1=失败,2=不可用。"""
    out = claim_once(ex, dry_run=dry_run)
    if ex.cred.expires_at:
        out.setdefault("token_expires_at", ex.cred.expires_at)
    code = 0 if out.get("result") in ("CLAIMED", "ALREADY", "DRY_RUN") else 1
    return out, code


def run_status(ex: Executor) -> tuple[RunOutcome, int]:
    """只查不领;退出码 0/1。"""
    out = check_status(ex)
    if ex.cred.expires_at:
        out.setdefault("token_expires_at", ex.cred.expires_at)
    return out, 0 if out.get("result") == "OK" else 1
