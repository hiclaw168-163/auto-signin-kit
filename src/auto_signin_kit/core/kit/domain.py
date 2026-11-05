"""SigninDomain 模板方法基类: 签到域的"Java 抽象类"(SPEC-11 追加)。

抽取自 trae/qoder/doubao 三域 run_action 的整段同构流程: 命令分发 →
doctor → 调度兜底跳过 → 凭证构建异常映射 → 动作分发 → 台账输出。
子类只实现 4 个钩子(凭证构建 / 签到 / 查询 / 体检),流程外壳锁死在
基类,这就是"把通用的抽出来,大家共用"的最终形态——新域只剩填空。

不适用的域: 命令集合或流程与"claim/status/doctor"同构差异大的
(如 workbuddy 的多动作编排),仍手写 run_action,不强迁。
"""

from __future__ import annotations

from typing import Any

from auto_signin_kit.core.dto import RunOutcome
from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.kit.accounts import account_enabled, resolve_account
from auto_signin_kit.core.kit.actions import DEFAULT_ACCOUNT, skip_done_today, unknown_action_report
from auto_signin_kit.core.logging import log_debug, result_log_path
from auto_signin_kit.core.report import emit_result


class DomainError(AutoSignInKitError):
    """域内可控失败: 携带结果标签与附加字段,由基类统一落台账并映射退出码。

    ``exit_code`` 语义沿用全库口径(0 成功/1 失败/2 不可用);登录态类失败
    (NO_AUTH)用 2,业务/过期失败用 1。
    """

    def __init__(self, result: str, report: str, *, exit_code: int = 1,
                 needs_attention: bool = True, extra: dict | None = None) -> None:
        """``result`` 是台账结果标签;``extra`` 并入输出 DTO。"""
        super().__init__(report)
        self.result = result
        self.report = report
        self.exit_code = exit_code
        self.needs_attention = needs_attention
        self.extra: dict = extra or {}


class SigninDomain:
    """签到域模板方法基类。

    子类必须提供 :attr:`name`/:attr:`display` 与 :meth:`build` 钩子;
    ``claim``/``status`` 未实现的动作自动按"不可用(2)"输出。所有台账
    输出、退出码语义、调度幂等跳过由基类保证,子类无感。
    """

    #: 域名(entry_points 注册名,全小写)。
    name: str = ""
    #: 人读显示名(台账与 CLI help)。
    display: str = ""
    #: 支持的内部动作;CLI 主入口 ``auto`` 是 ``claim`` 的别名。
    known_actions: tuple[str, ...] = ("claim", "status", "doctor")
    #: 脱敏登记表(None=无)。
    sensitive: set[str] | None = None
    #: 调度兜底触发时"当天已成功则跳过"的动作集合。
    skip_when_done: tuple[str, ...] = ("claim",)
    #: done_today 跳过日志里的量词(签到/领取)。
    ready_noun: str = "签到"

    # ------------------------------------------------------------ 钩子(子类实现)

    def build(self, config: Any, *, once: bool = False, dry_run: bool = False,
              account: str = DEFAULT_ACCOUNT) -> Any:
        """凭证 → API 客户端;失败抛 :class:`DomainError`(或 AutoSignInKitError)。

        ``once``/``dry_run`` 仅个别域消费(如豆包 once=网络错误不重试),
        用不上就忽略。``account``(SPEC-12 P2): 凭据 profile 化——具名账户
        按 AccountRef 的显式路径解析凭据,缺省 default 沿用现状链路。
        """
        raise NotImplementedError("子类必须实现 build()")

    def claim(self, api: Any, config: Any, *, dry_run: bool = False,
              once: bool = False) -> tuple[dict, int]:
        """执行一轮签到;返回 (结果 DTO, 退出码)。

        ``once`` 供"网络错误不重试"类域消费(如 qoder),用不上就忽略。
        """
        raise NotImplementedError("子类必须实现 claim()")

    def status(self, api: Any, config: Any) -> tuple[RunOutcome, int]:
        """只查状态,不修改;返回 (结果 DTO, 退出码)。"""
        raise NotImplementedError("子类必须实现 status()")

    def doctor(self, config: Any, *, account: str = DEFAULT_ACCOUNT) -> tuple[RunOutcome, int]:
        """离线体检(不联网);``account`` 指定体检哪个账户的凭据。默认未实现桩。"""
        return {"result": "ERROR",
                "report": "体检未实现: 请在 %s 域的 doctor() 钩子补齐" % self.name,
                "needs_attention": True}, 2

    # ------------------------------------------------------------ 基类设施

    def log_path(self):
        """silent 模式日志路径;`<NAME>_SIGNIN_LOG` 环境变量覆盖。"""
        return result_log_path(self.name, env_var="%s_SIGNIN_LOG" % self.name.upper())

    def _emit(self, out: RunOutcome, label: str, *, silent: bool,
              account: str | None = None) -> None:
        from auto_signin_kit.core.httpclient import last_request

        out = dict(out)
        out.setdefault("request", last_request())  # 本轮最后一次真实请求(已脱敏)
        emit_result(out, domain=self.name, action=label, silent=silent,
                    log_path=self.log_path(), sensitive=self.sensitive,
                    log_errors_without_silent=False, account=account)

    # ------------------------------------------------------------ 模板方法(锁定)

    def run_action(
        self, action: str, config: Any, *, silent: bool = False, dry_run: bool = False,
        once: bool = False, command: str | None = None,
        account: str = DEFAULT_ACCOUNT,
    ) -> int:
        """命令分发模板方法;结果按一行 JSON 契约输出,退出码 0/1/2,不抛业务异常。

        ``command`` 是用户/调度器实际敲的 CLI 命令名(如 ``auto``),用于
        台账 ``action`` 标识;缺省回落到内部 action 名。
        ``account``: 账户名(SPEC-12),未声明直接报错;停用账户静默调度跳过,
        手动执行放行。全程透传进台账信封。
        """
        label = command or action
        try:
            account = resolve_account(config, account)
        except AutoSignInKitError as exc:
            self._emit({"result": "FAILED",
                        "report": str(exc)}, label, silent=silent)
            return exc.exit_code
        # 停用账户只在静默(调度)路径跳过;显式 --account 手动跑永远放行
        if silent and not account_enabled(config, account):
            log_debug("%s/%s: 账户已停用,调度触发跳过(不执行、不落库)"
                      % (self.name, account))
            return 0
        if action not in self.known_actions:
            self._emit({"result": "ERROR", "needs_attention": True,
                        "report": unknown_action_report(action, self.known_actions)},
                       label, silent=silent, account=account)
            return 2
        if action == "doctor":
            out, code = self.doctor(config, account=account)
            self._emit(out, label, silent=silent, account=account)
            return code
        # 调度兜底触发(高密度时间点): 当天该账户已成功就不再执行,也不重复落库
        if skip_done_today(self.name, action, silent=silent,
                           skip_when_done=self.skip_when_done, noun=self.ready_noun,
                           account=account):
            return 0
        try:
            api = self.build(config, once=once, dry_run=dry_run, account=account)
        except DomainError as exc:
            self._emit({"result": exc.result, "needs_attention": exc.needs_attention,
                        "report": exc.report, **exc.extra}, label, silent=silent,
                       account=account)
            return exc.exit_code
        except AutoSignInKitError as exc:  # 域内旧异常兜底: 按退出码语义映射
            result = "NO_AUTH" if exc.exit_code == 2 else "ERROR"
            self._emit({"result": result, "needs_attention": True,
                        "report": str(exc)}, label, silent=silent, account=account)
            return exc.exit_code
        handler = {"claim": self.claim, "status": self.status}.get(action)
        is_stub = getattr(handler, "__func__", None) is (
            SigninDomain.claim if action == "claim" else SigninDomain.status)
        if handler is None or is_stub:  # known_actions 里声明了但钩子没实现
            self._emit({"result": "ERROR", "needs_attention": True,
                        "report": "动作 %s 未实现" % action}, label, silent=silent,
                       account=account)
            return 2
        out, code = handler(api, config, dry_run=dry_run, once=once) if action == "claim" \
            else handler(api, config)
        self._emit(out, label, silent=silent, account=account)
        return code
