"""qoder 域命令行入口: 只做参数解析、调 service、异常映射退出码。"""

from __future__ import annotations

import typer

from auto_signin_kit.core.config import load_domain_config_model
from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.logging import log_error
from auto_signin_kit.domains.qoder import service
from auto_signin_kit.domains.qoder.schema import QoderDomainConfig

app = typer.Typer(help="Qoder CN 每日 Credits 自动领取")


@app.callback()
def _root() -> None:
    """Qoder 域命令。"""
    # 空 callback 挡住 typer 对单命令应用的自动折叠(否则子命令名会被当参数吞掉)。


def _config() -> QoderDomainConfig:
    return load_domain_config_model("qoder", QoderDomainConfig, merge_local=True)


def _run(fn, *args, **kwargs):
    """统一异常处理: 业务异常转对应退出码。"""
    try:
        return fn(*args, **kwargs)
    except AutoSignInKitError as e:
        log_error(str(e))
        raise typer.Exit(e.exit_code) from e


@app.command()
def auto(
    dry_run: bool = typer.Option(False, "--dry-run", help="只报告可领取项,不真正领取"),
    once: bool = typer.Option(False, "--once", help="网络错误也不重试(计划任务下一轮再试)"),
    silent: bool = typer.Option(False, "--silent", help="结果写日志文件而非 stdout(配合 pythonw)"),
) -> None:
    """领取今日 Qoder CN Credits(幂等;重复运行只提示已领取)。"""
    raise typer.Exit(_run(service.run_action, "claim", _config(),
                          silent=silent, dry_run=dry_run, once=once))


@app.command()
def status() -> None:
    """只查活动列表,不领取。"""
    raise typer.Exit(_run(service.run_action, "status", _config()))


@app.command()
def doctor() -> None:
    """离线检查登录态可读性与设备组件(不联网、不领取)。"""
    raise typer.Exit(_run(service.run_action, "doctor", _config()))
