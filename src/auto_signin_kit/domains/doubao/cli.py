"""doubao 域命令行入口: 只做参数解析、调 service、异常映射退出码。"""

from __future__ import annotations

import typer

from auto_signin_kit.core.config import load_domain_config_model
from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.logging import log_error
from auto_signin_kit.domains.doubao import service
from auto_signin_kit.domains.doubao.schema import DoubaoDomainConfig

app = typer.Typer(help="豆包(电脑版)每日签到领额度")


@app.callback()
def _root() -> None:
    """doubao 域命令。"""
    # 空 callback 挡住 typer 对单命令应用的自动折叠(否则子命令名会被当参数吞掉)。


def _config() -> DoubaoDomainConfig:
    return load_domain_config_model("doubao", DoubaoDomainConfig, merge_local=True)


def _run(fn, *args, **kwargs):
    """统一异常处理: 业务异常转对应退出码。"""
    try:
        return fn(*args, **kwargs)
    except AutoSignInKitError as e:
        log_error(str(e))
        raise typer.Exit(e.exit_code) from e


@app.command()
def auto(
    dry_run: bool = typer.Option(False, "--dry-run", help="只查活动落地页可达性,不真正签到"),
    once: bool = typer.Option(False, "--once", help="网络错误也不重试(计划任务下一轮再试)"),
    silent: bool = typer.Option(False, "--silent", help="结果写日志文件而非 stdout(配合 pythonw)"),
) -> None:
    """领取今日豆包签到额度(幂等;重复运行只提示已签到)。"""
    raise typer.Exit(_run(service.run_action, "claim", _config(),
                          silent=silent, dry_run=dry_run, once=once, command="auto"))


@app.command()
def status() -> None:
    """查活动是否在期(只读,不签到)。"""
    raise typer.Exit(_run(service.run_action, "status", _config()))


@app.command()
def doctor() -> None:
    """离线检查 Cookie 解密链路与登录态(不联网、不签到)。"""
    raise typer.Exit(_run(service.run_action, "doctor", _config()))
