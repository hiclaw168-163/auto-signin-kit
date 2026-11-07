"""doubao 域业务逻辑: 签到领取/活动查询/体检。

**禁止 import typer**;交互走 ``core.prompt`` 注入,输出走 ``core.report``。
接口契约见 ``api.py`` 模块 docstring 与 docs/notes/NOTE-04。
流程外壳(命令分发/异常映射/台账输出/退出码)由 core.kit.domain 基类锁定,
本文件只实现 build/claim/status/doctor 四钩子。

签到通道(2026-11-07 定论,见 NOTE-04 §4.3): 默认走**客户端界面**(``uiaction``),
由客户端 JS SDK 生成 ``a_bogus`` 等签名;裸接口仅在客户端不可达时兜底。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_signin_kit.core.dto import RunOutcome
from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.httpclient import NO_NETWORK, last_request
from auto_signin_kit.core.kit.accounts import account_ref
from auto_signin_kit.core.kit.domain import DomainError, SigninDomain
from auto_signin_kit.core.logging import result_log_path
from auto_signin_kit.core.report import make_result
from auto_signin_kit.domains.client_signin.apps.doubao_cn import cdp, cookies, uiaction
from auto_signin_kit.domains.client_signin.apps.doubao_cn.api import DoubaoApi
from auto_signin_kit.domains.client_signin.apps.doubao_cn.schema import DoubaoDomainConfig


def _user_data_dir(config: DoubaoDomainConfig) -> Path | None:
    return Path(config.user_data_dir) if config.user_data_dir else None


#: 服务端"还没达标"的领奖回执话术(不是故障,归"暂不可领")。
_NOT_ELIGIBLE_HINTS = ("不符合领取条件", "暂不符合", "条件未满足", "尚未完成")


def _sweep_rewards(api: DoubaoApi, config: DoubaoDomainConfig) -> dict[str, Any]:
    """清扫活动任务奖励: 只领 ``task_status=2``(已完成待领取)的项。

    体验任务(生成文档/表格/PPT 等)须先在产品里真实完成对应操作才会变为
    可领取(status=2),清扫对未完成项不做任何事——不能凭空硬领。

    2026-11-07 补: 有些项(尤其里程碑)``task_status`` 已标 2,但服务端领奖时
    回"当前暂不符合领取条件"——那不是故障,是还没真正达标。这类回执归到
    ``blocked``(如实计入"暂不可领"),不写进 ``failures``,否则每次签到都会挂
    一行"领取失败",把真故障淹掉。
    """
    scode, body, _err = api.campaign_detail(config.campaign_code)
    payload = body if isinstance(body, dict) else {}
    if scode != 200 or payload.get("code") != 0:
        return {"error": "活动详情查询失败(http=%s code=%s)" % (scode, payload.get("code"))}
    data = payload.get("data") or {}
    items = (data.get("tasks") or []) + (data.get("milestones") or [])
    claimable = [t for t in items if t.get("task_status") == 2]
    claimed: list[dict[str, Any]] = []
    failures: list[str] = []
    blocked: list[str] = []
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
            continue
        message = str(biz.get("message") or "")
        if any(hint in message for hint in _NOT_ELIGIBLE_HINTS):
            blocked.append(title)
        else:
            failures.append("%s: %s" % (title, message or cerr or csc))
    pending = sum(1 for t in items if t.get("task_status") == 1)
    return {"claimed": claimed, "failures": failures, "blocked": blocked,
            "pending": pending}


def _daily_checkin(api: DoubaoApi, config: DoubaoDomainConfig) -> dict[str, Any] | None:
    """读 ``tasks[daily_signin].daily_checkin`` 子结构;查询失败/结构变动返回 None。"""
    scode, body, _err = api.campaign_detail(config.campaign_code)
    payload = body if isinstance(body, dict) else {}
    if scode != 200 or payload.get("code") != 0:
        return None
    for task in (payload.get("data") or {}).get("tasks") or []:
        if task.get("campaign_code") == "daily_signin":
            return task.get("daily_checkin") or {}
    return None


def _today_state(api: DoubaoApi, config: DoubaoDomainConfig) -> bool | None:
    """今日是否已签(只读);查询失败或结构变动返回 None。"""
    state = _daily_checkin(api, config)
    return None if state is None else bool(state.get("checked_in_today"))


def _resolve_channel(config: DoubaoDomainConfig) -> str:
    """签到通道: ``ui``(客户端自己发)或 ``http``(裸接口)。

    2026-11-07 定论: 活动页 SDK 的请求带 ``a_bogus`` 签名与 ``web_id``/
    ``tea_uuid``/``pc_version`` 等设备参数;裸 HTTP 缺这一层,服务端判为异常。
    现场现象更严重——反复发裸请求会牵连整个会话,**连客户端手工点都失效、
    重装客户端才恢复**。所以默认 ``auto``: 客户端带调试端口在跑就用 ``ui``,
    把签名交还客户端;只有客户端不可达才退回裸接口。
    """
    channel = (getattr(config, "checkin_channel", None) or "auto").lower()
    if channel in ("ui", "http"):
        return channel
    return "ui" if cdp.probe(cdp.resolve_port(config.cdp_port)) else "http"


def _checkin(api: DoubaoApi, config: DoubaoDomainConfig, *,
             dry_run: bool) -> tuple[RunOutcome, int]:
    """签到 + 任务奖励清扫;幂等接口,重复运行返回已签状态。"""
    if dry_run:
        scode, body, err = api.landing_page(config.campaign_code)
        ok = scode == 200 and isinstance(body, dict) and body.get("code") == 0
        return make_result(
            "DRY_RUN" if ok else "ERROR",
            "空跑: 活动落地页可达" if ok else "空跑失败: 网络/服务异常 %s" % (err or scode),
            dry_run=True, domain="doubao_cn", http=scode, needs_attention=not ok), 0 if ok else 1

    # 通道选择(2026-11-07): 客户端在跑就绝不用裸接口碰签到——见 _resolve_channel。
    if _resolve_channel(config) == "ui":
        return _checkin_via_ui(api, config)
    return _checkin_via_http(api, config)


def _checkin_via_ui(api: DoubaoApi,
                    config: DoubaoDomainConfig) -> tuple[RunOutcome, int]:
    """经客户端界面签到: 点真按钮,签名由客户端 JS 生成。

    签到接口的响应只回 ``null``+错误码时(签名缺失/风控),这里的``daily_checkin``
    才是唯一可信事实来源——奖励额度与连签天数也一并从它读,台账积分不断档。
    """
    state = _daily_checkin(api, config)
    if state is not None and state.get("checked_in_today"):
        return _success_tail(
            api, config, amount=(state.get("reward") or {}).get("reward_amount"),
            days=state.get("continuous_days"), idempotent=True,
            checkin_date=state.get("last_checkin_date"), prefix="今日已签到",
            channel="ui")
    ui = uiaction.signin_via_ui(config.cdp_port)
    if not ui.get("clicked"):
        return make_result(
            "ERROR",
            "界面通道不可用: %s;若客户端无法启动可设 checkin_channel: http" % ui.get("reason"),
            needs_attention=True, domain="doubao_cn", channel="ui"), 1
    state = _daily_checkin(api, config)
    if not (state or {}).get("checked_in_today"):
        return make_result(
            "ERROR",
            "已在客户端点击「%s」但活动详情仍未标记已签: 服务端/账号侧异常"
            "(可手工点一次确认;此前若用裸接口反复请求过,重装客户端可恢复)"
            % (ui.get("button") or "签到"),
            needs_attention=True, domain="doubao_cn", channel="ui-click"), 1
    return _success_tail(
        api, config, amount=(state.get("reward") or {}).get("reward_amount"),
        days=state.get("continuous_days"), idempotent=False,
        checkin_date=state.get("last_checkin_date"),
        prefix="签到成功(经客户端界面)", channel="ui-click")


def _checkin_via_http(api: DoubaoApi,
                      config: DoubaoDomainConfig) -> tuple[RunOutcome, int]:
    """裸接口通道: 直接 POST 签到端点(客户端不可用时的唯一选择)。"""
    scode, body, err = api.checkin(config.campaign_code)
    # 钉住"签到请求"快照: 后面若还要复查详情,last_request() 已被复查请求覆盖,
    # 台账里的 request 列就会误导排查(2026-11-07 实测踩到)。
    checkin_request = last_request()
    if scode == NO_NETWORK:
        return make_result("NETWORK", "网络不可达: %s" % (err or "unknown"),
                           needs_attention=True, domain="doubao_cn", err=err,
                           request=checkin_request), 1
    payload = body if isinstance(body, dict) else {}
    code = payload.get("code")
    data = payload.get("data") or {}
    if scode != 200 or code != 0:
        # 网关码映射: 710012* = 登录态失效(NO_AUTH,指引重开客户端恢复会话),
        # 其余按 ERROR。2026-11-03 排查实录: 服务端对过期会话在签到接口上
        # 会回误导性的 710010702"系统错误",710012001 才是明确过期;
        # 两值在台账词表(core.report.RESULT_LABELS)都有标签。
        #
        # 2026-11-07 补: 业务错误**不等于没签到**——服务端曾在受理后回错误码,
        # 且前端 SDK 已改为带 a_bogus 签名。故按序做两件事再下结论:
        #   ① 复查 detail.checked_in_today,已签就如实记 ALREADY;
        #   ② 复查不到再走界面兜底(客户端自己的签名点真按钮),再复查一次。
        no_auth = str(code or "").startswith("710012")
        base_msg = ("登录已过期(code=%s): 打开或重启豆包客户端恢复会话后重试"
                    % code) if no_auth else (
            "业务失败 code=%s msg=%s(http=%s)" % (
                code, payload.get("msg") or payload.get("message"), scode))
        if not no_auth and scode == 200:
            if _today_state(api, config) is True:
                return make_result(
                    "ALREADY",
                    "今日已签到(接口回 code=%s,复查状态已签,无需重复领取)" % code,
                    domain="doubao_cn", http=scode, biz_code=code,
                    channel="detail-verify", request=checkin_request), 0
            ui = uiaction.signin_via_ui(config.cdp_port)
            verified = _today_state(api, config) if ui.get("clicked") else None
            if verified is True:
                return make_result(
                    "CLAIMED",
                    "签到成功(经界面兜底通道: %s)" % ui.get("reason"),
                    domain="doubao_cn", channel="ui-click",
                    request=checkin_request), 0
            base_msg += "; 界面兜底: %s" % ui.get("reason")
            if ui.get("clicked"):
                base_msg += "(点击已送达但状态未变更,服务端仍未受理)"
        return make_result(
            "NO_AUTH" if no_auth else "ERROR", base_msg,
            needs_attention=True, domain="doubao_cn", http=scode, biz_code=code,
            request=checkin_request), 1

    return _success_tail(
        api, config, amount=data.get("reward_amount"),
        days=data.get("continuous_days"), idempotent=bool(data.get("idempotent")),
        checkin_date=data.get("checkin_date"), request=checkin_request, channel="http")


def _success_tail(api: DoubaoApi, config: DoubaoDomainConfig, *,
                  amount: Any, days: Any, idempotent: bool,
                  checkin_date: Any = None, request: dict | None = None,
                  prefix: str | None = None, channel: str | None = None
                  ) -> tuple[RunOutcome, int]:
    """签到成功后的统一收尾: 清扫任务奖励 + 积分口径 + 结果字段。"""
    # 签到成功/已签后,清扫活动任务奖励(详情查询失败不掩盖签到结果)
    sweep: dict[str, Any] = _sweep_rewards(api, config)
    parts: list[str] = []
    head = prefix or ("今日已签到" if idempotent else "签到成功")
    parts.append("%s +%s额度(连签%s天)" % (head, amount if amount is not None else "?",
                                          days if days is not None else "?"))
    result = "ALREADY" if idempotent else "CLAIMED"
    claimed = sweep.get("claimed") or []
    task_amount = sum(c["amount"] or 0 for c in claimed)
    if claimed:
        result = "CLAIMED"
        parts.append("任务奖励 %d 项 +%s额度(%s)" % (
            len(claimed), task_amount, "、".join(c["title"] for c in claimed)))
    if sweep.get("pending"):
        parts.append("%d 项任务未完成(完成后面板可领)" % sweep["pending"])
    if sweep.get("blocked"):
        parts.append("%d 项已达标记但服务端判定暂不符合条件(%s),未计为失败"
                     % (len(sweep["blocked"]), "、".join(sweep["blocked"])))
    if sweep.get("failures"):
        parts.append("领取失败: %s" % "; ".join(sweep["failures"]))
    if sweep.get("error"):
        parts.append("[%s]" % sweep["error"])
    # 积分方言(2026-11-05 补): credits = 签到所得 + 任务清扫所得——此前 doubao
    # 不回传方言字段,台账积分恒 0,积分统计少算了整个域(每日 +200)。
    earned = (amount or 0) + task_amount
    extra: dict[str, Any] = {}
    if channel:
        extra["channel"] = channel  # 台账能回答"这次是哪条通道签上的"
    out = make_result(result, "; ".join(parts), domain="doubao_cn",
                      checkin_date=checkin_date, reward_amount=amount,
                      continuous_days=days, task_rewards=claimed,
                      task_pending=sweep.get("pending", 0),
                      request=request, **extra)
    if earned:
        out["credits"] = earned
    if days is not None:
        out["streak_days"] = days
    return out, 0


def _status(api: DoubaoApi, config: DoubaoDomainConfig) -> tuple[RunOutcome, int]:
    """活动详情查询(只读): 活动是否在期 + 面板是否可用。"""
    scode, body, err = api.campaign_detail(config.campaign_code)
    payload = body if isinstance(body, dict) else {}
    code = payload.get("code")
    if scode == 200 and code == 0:
        return make_result("OK", "活动在期: %s" % config.campaign_code,
                           domain="doubao_cn", campaign_code=config.campaign_code), 0
    if scode == 200 and code == 2:
        return make_result("INACTIVE_ERROR",
                           "活动不在期或已换季(code=2 campaign not found): "
                           "请更新 config 的 campaign_code,见 NOTE-04",
                           needs_attention=True, domain="doubao_cn",
                           campaign_code=config.campaign_code), 1
    return make_result("ERROR", "查询失败 http=%s code=%s err=%s" % (scode, code, err),
                       needs_attention=True, domain="doubao_cn", http=scode), 1


class DoubaoDomain(SigninDomain):
    """doubao 域: 只实现四钩子,流程外壳(命令分发/异常映射/台账/退出码)由基类锁定。"""

    name = "doubao_cn"
    display = "豆包"
    known_actions = ("claim", "status", "doctor")

    def log_path(self) -> Any:
        """历史口径: doubao 的 silent 日志走通用 LOG_DIR 覆盖变量。"""
        return result_log_path(self.name, env_var="AUTO_SIGNIN_KIT_LOG_DIR")

    def build(self, config: Any, *, once: bool = False,
              dry_run: bool = False, account: str = "") -> DoubaoApi:
        """Cookie 解密 → DoubaoApi;``once``= 网络错误不重试。

        ``account``(SPEC-12 P2): 具名账户按 AccountRef 的 user_data_dir/
        cdp_port 解析(多开豆包各一份 User Data、各一个调试端口)。
        """
        ref = account_ref(config, account) if account else None
        root = Path(ref.user_data_dir) if ref and ref.user_data_dir else _user_data_dir(config)
        # 端口优先级: 具名账户显式 cdp_port > 域配置 cdp_port > 环境变量/默认探测
        effective_port = ref.cdp_port if ref and ref.cdp_port is not None else config.cdp_port
        try:
            cookie_header = cookies.load_cookie_header(root, cdp_port=effective_port)
        except AutoSignInKitError as exc:
            result = "NO_AUTH" if isinstance(exc, cookies.DoubaoCookieError) else "ERROR"
            raise DomainError(result, str(exc), exit_code=exc.exit_code) from exc
        budget = config.budget_seconds or 240.0
        return DoubaoApi(cookie_header, base_url=config.base_url,
                         budget=budget, retry=not once)

    def claim(self, api: DoubaoApi, config: Any, *, dry_run: bool = False,
              once: bool = False) -> tuple[dict, int]:
        """签到 + 任务奖励清扫。"""
        return _checkin(api, config, dry_run=dry_run)

    def status(self, api: DoubaoApi, config: Any) -> tuple[dict, int]:
        """活动详情查询(只读)。"""
        return _status(api, config)

    def doctor(self, config: Any, *, account: str = "") -> tuple[dict, int]:
        """离线体检: 平台/数据目录/Cookie 解密链路逐项检查(不联网)。"""
        ref = account_ref(config, account) if account else None
        root = Path(ref.user_data_dir) if ref and ref.user_data_dir else _user_data_dir(config)
        effective_port = ref.cdp_port if ref and ref.cdp_port is not None else config.cdp_port
        report = cookies.doctor_report(root, cdp_port=effective_port)
        ok = bool(report.get("session_ok"))
        return make_result(
            "OK" if ok else "ERROR",
            "体检通过" if ok else "体检未通过: %s" % report.get("session_error", "见 checks 字段"),
            needs_attention=not ok, domain="doubao_cn", checks=report), 0 if ok else 1


domain = DoubaoDomain()
#: 模块级入口(CLI 工厂与既有调用方引用): 即模板方法绑定。
run_action = domain.run_action
