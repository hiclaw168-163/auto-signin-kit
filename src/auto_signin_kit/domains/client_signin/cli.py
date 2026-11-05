"""client_signin 域命令行入口: run/status/doctor/list,应用经 --app 指定。

与 web_signin 引擎同构,只接管 ``kind: client`` 的声明(凭据来自本地
客户端);CDP 代码扩展(doubao_cn/workbuddy_cn/qoder)有各自独立的域
命令。list 的行渲染在 service(cli 只做参数解析与 echo,架构门禁:
cli 不 import 实现模块)。
"""

from __future__ import annotations

from typing import Annotated

import typer

from auto_signin_kit.core.kit.cli import run_guarded
from auto_signin_kit.domains.client_signin import service

app = typer.Typer(help="客户端声明式应用签到引擎: 本地客户端凭据,一份 YAML 接入一个应用")

#: 命令参数注解(--app 必填;--account 缺省 default)。
AppOpt = Annotated[str, typer.Option(..., "--app", metavar="APP_CODE",
                                     help="应用编码(config/apps/<app_code>.yaml)。")]
AccountOpt = Annotated[str, typer.Option("--account", metavar="NAME",
                                         help="应用 YAML accounts 里声明的账户名。")]


@app.callback()
def _root() -> None:
    """client_signin 域命令。"""
    # 空 callback 挡住 typer 对单命令应用的自动折叠。


@app.command()
def run(
    app_code: AppOpt,
    dry_run: bool = typer.Option(False, "--dry-run", help="只报告能否领取,不领取"),
    once: bool = typer.Option(False, "--once", help="网络错误也不重试(计划任务下一轮再试)"),
    silent: bool = typer.Option(False, "--silent", help="结果写日志文件而非 stdout"),
    account: AccountOpt = "default",
) -> None:
    """领取指定应用的每日签到(幂等;已签只提示不重复领取)。"""
    raise typer.Exit(run_guarded(service.run_action, "claim", None, app=app_code,
                                 silent=silent, dry_run=dry_run, once=once,
                                 command="run", account=account))


@app.command()
def status(app_code: AppOpt, account: AccountOpt = "default") -> None:
    """只查指定应用的签到状态,不领取。"""
    raise typer.Exit(run_guarded(service.run_action, "status", None, app=app_code,
                                 account=account))


@app.command()
def doctor(app_code: AppOpt, account: AccountOpt = "default") -> None:
    """离线检查指定应用的登录态可读性与有效期(不联网、不领取)。"""
    raise typer.Exit(run_guarded(service.run_action, "doctor", None, app=app_code,
                                 account=account))


@app.command("list")
def list_apps() -> None:
    """列出本引擎接管的声明式应用(编码/版本区/显示名/调度/登录态探测)。"""
    for line in service.list_lines():
        typer.echo(line)


if __name__ == "__main__":  # pragma: no cover
    app()
