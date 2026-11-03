"""scaffold 命令行入口: 生成新签到域骨架并注册(SPEC-11 阶段二)。

只做参数解析、调 scaffolding.service、异常映射退出码;生效编排交给
独立重建进程(本命令立即返回,不阻塞)。
"""

from __future__ import annotations

import typer

from auto_signin_kit.core.kit.cli import run_guarded
from auto_signin_kit.scaffolding import service

app = typer.Typer(help="脚手架: 生成新签到域骨架并注册(SPEC-11)")


@app.command("new")
def new(
    name: str = typer.Argument(..., help="域名(小写字母/数字/下划线,如 my_app)"),
    kind: str = typer.Option("web", "--kind", help="签到类型: web(HTTP) | client(本地客户端)"),
    display: str = typer.Option("", "--display", help="人读名(缺省用域名)"),
    base_url: str = typer.Option(..., "--base-url", help="目标站点接口宿主(http(s)://)"),
    daily_time: str = typer.Option("09:05", "--daily-time", help="每日调度触发时间(HH:MM)"),
) -> None:
    """生成域骨架 + 注册 entry-points,并后台重建生效。"""

    def _run() -> int:
        manifest = service.create_domain(name, kind, display or None, base_url, daily_time)
        from auto_signin_kit.scaffolding.rebuild import spawn_rebuild

        rebuilder = spawn_rebuild(wait_pid=None, reopen_serve=False)
        typer.echo(
            "已生成 %d 个文件并注册域 %s(%s)\n"
            "重建进程 pid=%d 正在后台执行 uv sync + tool 重装(约 1 分钟),\n"
            "完成后运行 auto-signin-kit %s --help 验证。\n"
            "签到逻辑是桩: 编辑 src/auto_signin_kit/domains/%s/service.py,"
            "完成后处理 tests/domains/%s/test_contract.py 的 xfail。"
            % (len(manifest.files), manifest.domain, kind, rebuilder, name, name, name))
        return 0

    raise typer.Exit(run_guarded(_run))
