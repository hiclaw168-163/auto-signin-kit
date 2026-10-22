"""trae 域命令行入口: 只做参数解析、调 service、异常映射退出码。"""

from __future__ import annotations

import typer

from auto_signin_kit.core.config import load_domain_config_model
from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.logging import log_error
from auto_signin_kit.domains.trae import service
from auto_signin_kit.domains.trae.schema import TraeDomainConfig

app = typer.Typer(help="Trae CN 每日签到自动领取")


@app.callback()
def _root() -> None:
    """Trae 域命令。"""
    # 空 callback 挡住 typer 对单命令应用的自动折叠(否则子命令名会被当参数吞掉)。


def _config() -> TraeDomainConfig:
    return load_domain_config_model("trae", TraeDomainConfig, merge_local=True)


def _run(fn, *args, **kwargs):
    """统一异常处理: 业务异常转对应退出码。"""
    try:
        return fn(*args, **kwargs)
    except AutoSignInKitError as e:
        log_error(str(e))
        raise typer.Exit(e.exit_code) from e


@app.command()
def auto(
    dry_run: bool = typer.Option(False, "--dry-run", help="只报告签到状态,不领取"),
    once: bool = typer.Option(False, "--once", help="保留参数位,与 qoder 域行为对齐"),
    silent: bool = typer.Option(False, "--silent", help="结果写日志文件而非 stdout(配合 pythonw)"),
) -> None:
    """领取今日 Trae CN 签到(幂等;已签只提示不重复领取)。"""
    raise typer.Exit(_run(service.run_action, "claim", _config(),
                          silent=silent, dry_run=dry_run, once=once, command="auto"))


@app.command()
def status() -> None:
    """只查今日签到状态,不领取。"""
    raise typer.Exit(_run(service.run_action, "status", _config()))


@app.command()
def doctor() -> None:
    """离线检查登录态可读性与有效期(不联网、不领取)。"""
    raise typer.Exit(_run(service.run_action, "doctor", _config()))
