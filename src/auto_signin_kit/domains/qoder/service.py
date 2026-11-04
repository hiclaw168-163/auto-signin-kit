"""Qoder CN 每日 Credits: 活动查询 → 分类 → 幂等领取 → 一行 JSON。

对策见 NOTE-02(qoder 积分面取证)的结论: /sash/api/v1/me/campaigns;
缺 ``Cosy-ClientType: 10`` 头服务端返回空列表;领取 POST 默认不重试防重复。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from auto_signin_kit.core.httpclient import Budget
from auto_signin_kit.core.kit.domain import DomainError, SigninDomain
from auto_signin_kit.core.kit.transport import transport_error_dto
from auto_signin_kit.domains.qoder.api import (
    SENSITIVE,
    Api,
    build_api,
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


def _auth_reports() -> dict[int, str]:
    """401/403 的域专属指引(端点不匹配的排查提示是 Qoder 特有问题)。"""
    return {401: "登录态被服务端拒绝（HTTP 401）。常见原因：令牌已过期，"
                 "打开 Qoder 桌面端重新登录；或端点与客户端版本不匹配"
                 "（CN 版/国际版），可用 QODER_BASE_URL 覆盖后重试"}


def claim_once(api: Api, budget: Budget, *, retry: bool = True, dry_run: bool = False) -> dict:
    """查询 → 分类 → 领取/汇报。结果: CLAIMED/ALREADY/PENDING/INACTIVE/DRY_RUN。"""
    code, payload, err = api.campaigns(budget=budget, retry=retry)
    transport = transport_error_dto(code, payload, err, auth_reports=_auth_reports())
    if transport is not None:
        return transport
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
    transport = transport_error_dto(code, payload, err, auth_reports=_auth_reports())
    if transport is not None:
        return transport, 1
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


class QoderDomain(SigninDomain):
    """qoder 域: 只实现四钩子,流程外壳(命令分发/异常映射/台账/退出码)由基类锁定。"""

    name = "qoder"
    display = "Qoder CN"
    sensitive = SENSITIVE
    ready_noun = "领取"
    #: 领取窗口按每天 10:00 轮换(非自然日),台账 done_today(自然日)会误判:
    #: 当天 00:05 领到的是"昨天的窗口",10:00 开新窗口后的调度触发全部被
    #: 误跳过,真实窗口要拖到次日才领。claim_once 本身服务端真值驱动
    #: (只 POST CLAIMABLE,领过返回 ALREADY),跳过判断交给服务端,关闭日历日跳过。
    skip_when_done = ()

    def build(self, config: Any, *, once: bool = False, dry_run: bool = False) -> Any:
        """登录态 → Api;三种凭证失败按语义转 DomainError。"""
        try:
            api, sess, staleness = build_api(config)
        except NoAuthError as exc:
            raise DomainError("NO_AUTH", str(exc), exit_code=2,
                              extra={"looked_in": exc.looked_in or None}) from exc
        except QoderAuthError as exc:
            raise DomainError("AUTH_ERROR", str(exc), exit_code=1,
                              extra={"looked_in": exc.looked_in or None}) from exc
        if staleness == "EXPIRED":
            raise DomainError(
                "AUTH_REJECTED", "登录态已过期，请打开 Qoder CN 桌面端重新登录",
                exit_code=1,
                extra={"token_expires_at": sess.get("expiresAt") if sess else None})
        return api

    def claim(self, api: Any, config: Any, *, dry_run: bool = False,
              once: bool = False) -> tuple[dict, int]:
        """领取 Credits;``once``= 网络错误也不重试(计划任务下一轮再试)。"""
        budget, warning = self._budget(config)
        out = claim_once(api, budget, retry=not once, dry_run=dry_run)
        if api.token_expires_at:
            out.setdefault("token_expires_at", api.token_expires_at)
        code = 0 if out.get("result") in ("CLAIMED", "ALREADY", "INACTIVE", "DRY_RUN") else 1
        if warning:
            out.setdefault("config_warning", warning)
        return out, code

    def status(self, api: Any, config: Any) -> tuple[dict, int]:
        """只查活动列表,不领取。"""
        budget, _ = self._budget(config)
        return run_status(api, budget)

    def doctor(self, config: Any) -> tuple[dict, int]:
        """离线检查登录态可读性与设备组件。"""
        from auto_signin_kit.domains.qoder.doctor import run_doctor

        return run_doctor()

    def _budget(self, config: Any) -> tuple[Budget, str | None]:
        value, warning = parse_budget(config)
        return Budget(value), warning


domain = QoderDomain()
#: 模块级入口(CLI 工厂与既有调用方引用): 即模板方法绑定。
run_action = domain.run_action
