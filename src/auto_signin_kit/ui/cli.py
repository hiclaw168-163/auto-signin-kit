"""ui 命令行入口: 只做参数解析与依赖缺失的显式报错(SPEC-09)。

fastapi/uvicorn 属于 ``[web]`` extra,不在基础安装里——缺了就明确告诉用户
怎么装,不做静默兜底(AGENTS.md 硬性规则 4)。
"""

from __future__ import annotations

import typer

from auto_signin_kit.core.errors import ConfigError
from auto_signin_kit.core.logging import log_error

app = typer.Typer(help="Web 管理台: 本地起服务,浏览器管理总览/台账/定时任务/配置/体检")


@app.callback()
def _root() -> None:
    """ui 命令。"""
    # 空 callback 挡住 typer 对单命令应用的自动折叠(否则子命令名会被当参数吞掉)。


def _run(fn, *args, **kwargs):
    """统一异常处理: 业务/环境异常转对应退出码。"""
    try:
        return fn(*args, **kwargs)
    except ConfigError as e:
        log_error(str(e))
        raise typer.Exit(e.exit_code) from e


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="监听地址;默认只绑本机,不对外"),
    port: int = typer.Option(8688, help="监听端口"),
    open_browser: bool = typer.Option(True, "--open/--no-open",
                                      help="服务就绪后自动打开浏览器"),
    daemon: bool = typer.Option(False, "--daemon",
                                help="后台运行: 拉起守护进程后本命令退出"),
    daemon_child: bool = typer.Option(False, "--daemon-child", hidden=True),
) -> None:
    """启动本地 Web 管理台(需要 [web] extra: uv tool install 'auto-signin-kit[web]')。"""
    _run(_serve, host, port, open_browser, daemon, daemon_child)


def _serve(host: str, port: int, open_browser: bool,
           daemon: bool, daemon_child: bool) -> None:
    from auto_signin_kit.ui import daemon as daemon_mod

    if daemon and daemon_child:
        raise ConfigError("--daemon 与 --daemon-child 不能同时使用")
    if daemon_child:
        daemon_mod.run_child(host, port, open_browser)
        return
    if daemon:
        typer.echo(daemon_mod.start_daemon(host, port, open_browser))
        return
    _foreground(host, port, open_browser)


def _foreground(host: str, port: int, open_browser: bool) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise ConfigError(
            "Web 管理台依赖未安装(fastapi/uvicorn 属于 [web] extra)。"
            "执行: uv tool install 'auto-signin-kit[web]' --force"
        ) from exc
    from auto_signin_kit.ui.app import create_app

    if open_browser:
        import threading
        import webbrowser

        url = "http://%s:%d" % (host, port)
        # 延迟打开: 等 uvicorn 进入事件循环,否则浏览器抢跑拿到连接拒绝。
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


@app.command()
def stop() -> None:
    """停止后台运行的 Web 管理台(幂等;前台进程请直接 Ctrl+C)。"""
    _run(_stop)


def _stop() -> None:
    from auto_signin_kit.ui import daemon as daemon_mod

    typer.echo(daemon_mod.stop_service())


@app.command()
def status() -> None:
    """查看后台服务的运行状态与日志路径。"""
    _run(_status)


def _status() -> None:
    from auto_signin_kit.ui import daemon as daemon_mod

    typer.echo(daemon_mod.status_text())


@app.command()
def build(
    if_missing: bool = typer.Option(
        False, "--if-missing",
        help="已构建则跳过(一键启动脚本用;不带它则已构建也重建)"),
) -> None:
    """构建前端产物(npm install + build;需要 Node.js 18+)。"""
    _run(_build, if_missing)


def _build(if_missing: bool) -> None:
    from auto_signin_kit.ui import build as build_mod

    if build_mod.ensure_built(if_missing):
        typer.echo("已构建 frontend/dist")
    else:
        typer.echo("frontend/dist 已存在,跳过(要强制重建先删掉 dist 目录)")
