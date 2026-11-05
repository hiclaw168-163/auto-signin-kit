"""workbuddy 域命令行入口: 只做参数解析、调 service、异常映射退出码。"""

from __future__ import annotations

from typing import Any

import typer

from auto_signin_kit.core.config import load_domain_config_model
from auto_signin_kit.core.kit.cli import run_guarded as _run
from auto_signin_kit.domains.workbuddy_cn import service
from auto_signin_kit.domains.workbuddy_cn.schema import WorkbuddyDomainConfig

app = typer.Typer(help="WorkBuddy 每日签到与成长中心自动化")


@app.callback()
def _root() -> None:
    """WorkBuddy 域命令。"""
    # 空 callback 挡住 typer 对单命令应用的自动折叠(否则子命令名会被当参数吞掉)。


def _config() -> WorkbuddyDomainConfig:
    return load_domain_config_model("workbuddy_cn", WorkbuddyDomainConfig, merge_local=True)


def _account_opt() -> Any:
    """账户选项工厂(SPEC-12): 每命令独立实例;裁决在 service.run_action。"""
    return typer.Option(
        "default", "--account", metavar="NAME",
        help="多账户: 配置 accounts 里声明的账户名(缺省 default)。")


@app.command()
def auto(
    silent: bool = typer.Option(
        False, "--silent", help="结果写日志文件而非 stdout(配合 pythonw 定时任务)"
    ),
    account: str = _account_opt(),
) -> None:
    """签到 + 成长中心(每日主入口;幂等,重复运行不会多领)。"""
    action = "silent" if silent else "auto"
    raise typer.Exit(_run(service.run_action, action, _config(), silent=silent,
                          account=account))


@app.command()
def poll(account: str = _account_opt()) -> None:
    """轮询: 补签(未签才签) + 成长中心;结果静默写日志。"""
    raise typer.Exit(_run(service.run_action, "silent-poll", _config(), silent=True,
                          account=account))


@app.command()
def growth(account: str = _account_opt()) -> None:
    """仅成长中心(不签到)。"""
    raise typer.Exit(_run(service.run_action, "growth", _config(), account=account))


@app.command()
def active(
    silent: bool = typer.Option(
        False, "--silent", help="结果写日志文件而非 stdout(配合 pythonw 定时任务)"
    ),
    account: str = _account_opt(),
) -> None:
    """今日活跃保底: 查活跃记录,没有就自动补一次对话(活跃天数只认对话)。"""
    raise typer.Exit(_run(service.run_action, "active", _config(), silent=silent,
                          account=account))


@app.command()
def status(account: str = _account_opt()) -> None:
    """仅查签到状态(调试;不领取)。"""
    raise typer.Exit(_run(service.run_action, "status", _config(), account=account))


@app.command()
def claim(account: str = _account_opt()) -> None:
    """仅领取签到(调试;幂等)。"""
    raise typer.Exit(_run(service.run_action, "claim", _config(), account=account))


@app.command("all")
def all_info(account: str = _account_opt()) -> None:
    """status + claim(调试;输出原始返回)。"""
    raise typer.Exit(_run(service.run_action, "all", _config(), account=account))


@app.command()
def doctor(account: str = _account_opt()) -> None:
    """离线检查凭据格式与解密运行时能力(不解密、不联网)。"""
    raise typer.Exit(_run(service.run_action, "doctor", _config(), account=account))
