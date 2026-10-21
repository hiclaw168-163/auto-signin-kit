"""schedule 域命令行入口: 只做参数解析、调 service、异常映射退出码。

**不要在这里写业务逻辑**——包括"看起来无害"的前置校验;这类代码放这里会让
service 层单测 mock 不到真实调用路径(门禁
``test_domain_cli_files_do_not_import_business_modules`` 就是拦这个的)。
"""

from __future__ import annotations

import typer

from auto_signin_kit.core.config import load_domain_config_model
from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.logging import log_error
from auto_signin_kit.domains.schedule import service
from auto_signin_kit.domains.schedule.schema import ScheduleDomainConfig

app = typer.Typer(help="定时调度: 注册/注销/查看各域的每日自动任务")


@app.callback()
def _root() -> None:
    """schedule 域命令。"""
    # 空 callback 挡住 typer 对单命令应用的自动折叠(否则子命令名会被当参数吞掉)。


def _config() -> ScheduleDomainConfig:
    return load_domain_config_model("schedule", ScheduleDomainConfig, merge_local=True)


def _run(fn, *args, **kwargs):
    """统一异常处理: 业务异常转对应退出码,而不是笼统 1。"""
    try:
        return fn(*args, **kwargs)
    except AutoSignInKitError as e:
        log_error(str(e))
        raise typer.Exit(e.exit_code) from e


@app.command()
def install(
    domain: str | None = typer.Option(None, "--domain", "-d",
                                      help="只注册指定域(workbuddy / qoder / trae);默认全部"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只报告会注册什么,不改动系统"),
) -> None:
    """注册定时任务;已存在则覆盖更新(幂等,可重复执行)。"""
    raise typer.Exit(_run(service.run_action, "install", _config(),
                          domain=domain, dry_run=dry_run))


@app.command()
def uninstall(
    domain: str | None = typer.Option(None, "--domain", "-d",
                                      help="只注销指定域(workbuddy / qoder / trae);默认全部"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只报告会注销什么,不改动系统"),
) -> None:
    """注销定时任务;原本没注册也算成功(幂等)。"""
    raise typer.Exit(_run(service.run_action, "uninstall", _config(),
                          domain=domain, dry_run=dry_run))


@app.command()
def status(
    domain: str | None = typer.Option(None, "--domain", "-d",
                                      help="只看指定域(workbuddy / qoder / trae);默认全部"),
) -> None:
    """查看各任务是否已注册;未注册是正常状态,不是错误。"""
    raise typer.Exit(_run(service.run_action, "status", _config(), domain=domain))


@app.command()
def render(
    domain: str | None = typer.Option(None, "--domain", "-d",
                                      help="只渲染指定域(workbuddy / qoder / trae);默认全部"),
) -> None:
    """打印将要写入系统的调度定义(XML/plist/crontab),不落盘、不注册。"""
    text = _run(service.render_definitions, _config(), domain)
    typer.echo(text)
