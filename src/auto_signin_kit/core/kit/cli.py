"""域 CLI 工厂: 生成标准 ``auto/status/doctor`` 三命令应用(多域共享)。

抽取自 trae/doubao/qoder 三域逐字同构的 cli.py(2026-11-03 四域共性
分析结论 #1)。工厂只做参数解析、调 service、异常映射退出码(AGENTS
规则 2 的 cli 分层不变);命令集合不同的域(如 workbuddy/schedule)
仍手写 cli.py,只复用 :func:`run_guarded`。
"""

from __future__ import annotations

from typing import Any

import typer

from auto_signin_kit.core.config import load_domain_config_model
from auto_signin_kit.core.config.schema import DomainConfig
from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.logging import log_error


def run_guarded(fn: Any, *args: Any, **kwargs: Any) -> int:
    """统一异常处理: 业务异常转对应退出码(0 成功 / 1 失败 / 2 不可用)。"""
    try:
        return fn(*args, **kwargs)
    except AutoSignInKitError as e:
        log_error(str(e))
        raise typer.Exit(e.exit_code) from e


def create_domain_app(
    domain: str,
    display: str,
    config_cls: type[DomainConfig],
    service: Any,
    *,
    app_help: str | None = None,
    auto_help: str | None = None,
    dry_run_help: str = "只报告签到状态,不领取",
    once_help: str = "网络错误也不重试(计划任务下一轮再试)",
    status_help: str = "只查今日签到状态,不领取。",
    doctor_help: str = "离线检查登录态可读性与有效期(不联网、不领取)。",
) -> typer.Typer:
    """生成标准三命令域 CLI。

    ``service`` 需提供 ``run_action(action, config, **opts) -> int``;
    help 文案缺省按 ``display`` 推导,域专属措辞经参数覆盖。
    """
    app = typer.Typer(help=app_help or ("%s 每日签到自动领取" % display))

    @app.callback()
    def _root() -> None:
        """%s 域命令。""" % domain
        # 空 callback 挡住 typer 对单命令应用的自动折叠(否则子命令名会被当参数吞掉)。

    def _config() -> DomainConfig:
        return load_domain_config_model(domain, config_cls, merge_local=True)

    @app.command()
    def auto(
        dry_run: bool = typer.Option(False, "--dry-run", help=dry_run_help),
        once: bool = typer.Option(False, "--once", help=once_help),
        silent: bool = typer.Option(False, "--silent",
                                    help="结果写日志文件而非 stdout(配合 pythonw)"),
    ) -> None:
        """%s""" % (auto_help or ("领取今日 %s 签到(幂等;已签只提示不重复领取)。" % display))
        raise typer.Exit(run_guarded(service.run_action, "claim", _config(),
                                     silent=silent, dry_run=dry_run, once=once,
                                     command="auto"))

    @app.command()
    def status() -> None:
        """%s""" % status_help
        raise typer.Exit(run_guarded(service.run_action, "status", _config()))

    @app.command()
    def doctor() -> None:
        """%s""" % doctor_help
        raise typer.Exit(run_guarded(service.run_action, "doctor", _config()))

    return app
