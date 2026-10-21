"""workbuddy 域命令行入口: 只做参数解析、调 service、异常映射退出码。"""

from __future__ import annotations

import typer

from auto_signin_kit.core.config import load_domain_config_model
from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.logging import log_error
from auto_signin_kit.domains.workbuddy import service
from auto_signin_kit.domains.workbuddy.schema import WorkbuddyDomainConfig

app = typer.Typer(help="WorkBuddy 每日签到与成长中心自动化")


@app.callback()
def _root() -> None:
    """WorkBuddy 域命令。"""
    # 空 callback 挡住 typer 对单命令应用的自动折叠(否则子命令名会被当参数吞掉)。


def _config() -> WorkbuddyDomainConfig:
    return load_domain_config_model("workbuddy", WorkbuddyDomainConfig, merge_local=True)


def _run(fn, *args, **kwargs):
    """统一异常处理: 业务异常转对应退出码。"""
    try:
        return fn(*args, **kwargs)
    except AutoSignInKitError as e:
        log_error(str(e))
        raise typer.Exit(e.exit_code) from e


@app.command()
def auto(
    silent: bool = typer.Option(
        False, "--silent", help="结果写日志文件而非 stdout(配合 pythonw 定时任务)"
    ),
) -> None:
    """签到 + 成长中心(每日主入口;幂等,重复运行不会多领)。"""
    action = "silent" if silent else "auto"
    raise typer.Exit(_run(service.run_action, action, _config(), silent=silent))


@app.command()
def poll(
    empty_log: bool = typer.Option(False, "--empty-log", help="空跑(已签+无可领取项)也写日志"),
) -> None:
    """轮询: 补签(未签才签) + 成长中心;结果静默写日志。"""
    import os

    if empty_log:
        os.environ.setdefault("WORKBUDDY_GROWTH_LOG_EMPTY", "1")
    raise typer.Exit(_run(service.run_action, "silent-poll", _config(), silent=True))


@app.command()
def growth() -> None:
    """仅成长中心(不签到)。"""
    raise typer.Exit(_run(service.run_action, "growth", _config()))


@app.command()
def active(
    silent: bool = typer.Option(
        False, "--silent", help="结果写日志文件而非 stdout(配合 pythonw 定时任务)"
    ),
) -> None:
    """查今日活跃度: 今天是否已完成一次对话(活跃天数只认对话,领积分不算)。"""
    raise typer.Exit(_run(service.run_action, "active", _config(), silent=silent))


@app.command()
def status() -> None:
    """仅查签到状态(调试;不领取)。"""
    raise typer.Exit(_run(service.run_action, "status", _config()))


@app.command()
def claim() -> None:
    """仅领取签到(调试;幂等)。"""
    raise typer.Exit(_run(service.run_action, "claim", _config()))


@app.command("all")
def all_info() -> None:
    """status + claim(调试;输出原始返回)。"""
    raise typer.Exit(_run(service.run_action, "all", _config()))


@app.command()
def doctor() -> None:
    """离线检查凭据格式与解密运行时能力(不解密、不联网)。"""
    raise typer.Exit(_run(service.run_action, "doctor", _config()))
