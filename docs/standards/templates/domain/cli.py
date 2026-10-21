"""workbuddy 域命令行入口。

cli.py 只做三件事: 解析参数、调用 service、把异常映射成合适的退出码。
**不要在这里写业务逻辑**——包括"看起来无害"的校验逻辑,比如直接调用某个
底层函数做前置检查——这类代码放在这里会导致 service 层的单测 mock 覆盖不到它,
真实执行时才会暴露问题(这是真实踩过的坑,不是假设)。
"""

from __future__ import annotations

import typer

from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.logging import log_error
from auto_signin_kit.domains.workbuddy import service

app = typer.Typer(help="workbuddy 域命令")


@app.callback()
def _root() -> None:
    """workbuddy 域命令。"""
    # 这个空 callback 不是摆设: 只有一个 @app.command() 的 Typer 应用会被
    # typer/click 自动"折叠"成单命令模式,导致 `auto-signin-kit workbuddy run 参数`
    # 这种带子命令名的调用方式失效(命令名会被当成参数吞掉)。
    # 加了这个 callback 之后 app 才会老老实实按多子命令的方式解析,
    # 以后往这个域加第二个命令时也不用再改这里。


def _run(fn, *args, **kwargs):
    """统一异常处理: 业务异常转对应退出码,而不是笼统 1。"""
    try:
        return fn(*args, **kwargs)
    except AutoSignInKitError as e:
        log_error(str(e))
        raise typer.Exit(e.exit_code) from e


@app.command()
def run(arg: str = typer.Argument(..., help="示例参数")) -> None:
    """示例命令,替换成真正的域命令。"""
    result = _run(service.do_something, arg)
    typer.echo(result)
