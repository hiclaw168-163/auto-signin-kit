"""ui 命令行入口: 只做参数解析与依赖缺失的显式报错(SPEC-09)。

fastapi/uvicorn 属于 ``[web]`` extra,不在基础安装里——缺了就明确告诉用户
怎么装,不做静默兜底(AGENTS.md 硬性规则 4)。
"""

from __future__ import annotations

import typer

from auto_signin_kit.core.errors import ConfigError
from auto_signin_kit.core.logging import log_error

app = typer.Typer(help="Web 管理台: 本地起服务,浏览器管理看板/调度/历史")


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
) -> None:
    """启动本地 Web 管理台(需要 [web] extra: uv tool install 'auto-signin-kit[web]')。"""
    _run(_serve, host, port)


def _serve(host: str, port: int) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise ConfigError(
            "Web 管理台依赖未安装(fastapi/uvicorn 属于 [web] extra)。"
            "执行: uv tool install 'auto-signin-kit[web]' --force"
        ) from exc
    from auto_signin_kit.ui.app import create_app

    uvicorn.run(create_app(), host=host, port=port, log_level="info")
