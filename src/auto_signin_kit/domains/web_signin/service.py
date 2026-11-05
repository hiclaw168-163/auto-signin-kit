"""web_signin 服务层: 每份应用声明实例化一个 DeclarativeDomain。

复用 core 的 SigninDomain 模板方法基类——账户解析/停用跳过/调度幂等/
台账输出/退出码映射全部由基类承担;引擎只实现四个钩子(凭证/签到/查询/
体检),并把 ``name`` 指到 ``app_code``: 台账 ``domain`` 列、``ask_apps``
主档、日志名三处天然对齐,不存在"域名换应用编码"的转换层。
"""

from __future__ import annotations

from typing import Any

from auto_signin_kit.core.dto import RunOutcome
from auto_signin_kit.core.httpclient import Budget
from auto_signin_kit.core.kit.domain import SigninDomain
from auto_signin_kit.domains.web_signin import apps, authkit, executor
from auto_signin_kit.domains.web_signin.schema import AppDeclaration

#: 退出码 0=成功或幂等的结果集合(对齐全库口径)。
_OK_RESULTS = ("CLAIMED", "ALREADY", "DRY_RUN")


class WebSigninDomain(SigninDomain):
    """一份声明式应用的运行期域对象;台账/调度/CLI 全部按 app_code 归位。"""

    def __init__(self, decl: AppDeclaration) -> None:
        """app_code 即台账 domain 列;敏感值集合随凭据解析动态填充。"""
        self.decl = decl
        self.name = decl.app_code
        self.display = decl.display
        self.ready_noun = decl.ready_noun
        self.sensitive: set[str] = set()  # 凭据解析时动态登记令牌

    # ------------------------------------------------------------ 四钩子

    def build(self, config: Any, *, once: bool = False, dry_run: bool = False,
              account: str = "") -> executor.Executor:
        """声明 + 账户 → Executor;认证失败按语义转 DomainError。"""
        del once  # 查询+领取共两次请求,预算内完成
        from auto_signin_kit.core.kit.accounts import account_ref

        ref = account_ref(self.decl, account) if account != "default" else None
        skip_env = ref is not None  # 具名账户隔离环境变量令牌(那是 default 户的)
        try:
            cred = authkit.resolve(self.decl, ref, skip_env=skip_env)
        except authkit.NoAuthError as exc:
            raise exc.to_domain_error() from exc
        except authkit.AuthBrokenError as exc:
            raise exc.to_domain_error() from exc
        except authkit.AuthRejectedError as exc:
            raise exc.to_domain_error() from exc
        if cred.token:
            self.sensitive.add(cred.token)
        return executor.Executor(self.decl, cred, Budget(self.decl.budget_seconds))

    def claim(self, api: executor.Executor, config: Any, *, dry_run: bool = False,
              once: bool = False) -> tuple[RunOutcome, int]:
        """领取一轮(状态先行/幂等/限流退避/复查确认)。"""
        del once, config
        return executor.run_claim(api, dry_run=dry_run)

    def status(self, api: executor.Executor, config: Any) -> tuple[RunOutcome, int]:
        """只查今日状态,不领取。"""
        del config
        return executor.run_status(api)

    def doctor(self, config: Any, *, account: str = "") -> tuple[RunOutcome, int]:
        """离线体检登录态可读性与有效期(不联网)。"""
        del config
        from auto_signin_kit.core.kit.accounts import account_ref

        ref = account_ref(self.decl, account) if account != "default" else None
        return authkit.doctor(self.decl, ref, skip_env=ref is not None)

    def _budget(self, config: Any) -> tuple[Budget, str | None]:
        del config
        return Budget(self.decl.budget_seconds), None


def domain_for(app_code: str) -> WebSigninDomain:
    """编码 → 运行期域对象;声明不存在/非法给带出路的报错。"""
    return WebSigninDomain(apps.load_app(app_code))


def run_action(
    action: str, config: Any, *, app: str = "", silent: bool = False,
    dry_run: bool = False, once: bool = False, command: str | None = None,
    account: str = "default",
) -> int:
    """引擎统一入口: ``--app`` 指定声明应用,其余交基类模板方法。

    ``config`` 是引擎域自身的 DomainConfig(当前无引擎级字段,占位保持
    基类签名);账户解析/停用跳过用的"配置"是应用声明本身——账户名单
    就声明在应用 YAML 的 ``accounts`` 里。
    """
    if not app:
        from auto_signin_kit.core.errors import CommandError

        raise CommandError("缺少 --app 参数: 声明式应用必须指定 app_code"
                           "（用 `web_signin list` 查看可用应用）")
    domain = domain_for(app)
    return domain.run_action(action, domain.decl, silent=silent, dry_run=dry_run,
                             once=once, command=command, account=account)


def list_lines() -> list[str]:
    """``list`` 命令的行渲染;坏声明以"无效"行如实呈现,不静默吞掉。"""
    from auto_signin_kit.core.kit.actions import app_enabled

    declarations, broken = apps.all_apps_safe()
    lines = ["无效  %s" % path for path in broken]
    for decl in declarations:
        auth_ok = "✓" if authkit.ready(decl) else "×"
        enabled = "" if app_enabled(decl.app_code) else "  [已停用]"
        times = "/".join(decl.schedule.times)
        accounts = "" if len(decl.accounts) <= 1 else \
            " 账户:%s" % ",".join(ref.name for ref in decl.accounts)
        lines.append("%-12s %-4s %-14s 登录态:%s  %s%s%s"
                     % (decl.app_code, decl.region, decl.display, auth_ok,
                        times, accounts, enabled))
    if not lines:
        lines.append("config/apps/ 下还没有应用声明;"
                     "规范见 docs/standards/STD-07-声明式应用接入.md")
    return lines
