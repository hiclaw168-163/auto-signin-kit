"""init 命令行入口: 只做参数解析、调 service、渲染结果与退出码。"""

from __future__ import annotations

import sys

import typer

from auto_signin_kit.core.kit.cli import run_guarded as _run
from auto_signin_kit.init.service import run_init

app = typer.Typer(help="一键初始化: 环境自检 → 选域 → 客户端就绪检查 → 注册调度 → 拉起管理台")


@app.callback(invoke_without_command=True)
def init(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过交互: 全部域启用并直接执行"),
    no_ui: bool = typer.Option(False, "--no-ui", help="不启动 Web 管理台"),
) -> None:
    """新环境一键就位;可重复执行(注册幂等,已就绪的步骤自动跳过)。"""
    if ctx.invoked_subcommand is not None:
        return
    interactive = not yes and sys.stdin.isatty()
    raise typer.Exit(_run(_execute, interactive, not no_ui))


def _execute(interactive: bool, start_ui: bool) -> int:
    result = run_init(interactive, start_ui)
    _render(result)
    return int(result["exit"])


def _render(result: dict) -> None:  # type: ignore[type-arg]
    typer.echo("== 环境自检 ==")
    for check in result["checks"]:
        mark = "✓" if check["ok"] else "✗"
        typer.echo("  %s %s: %s" % (mark, check["name"], check.get("detail", "")))
    enabled = result["enabled"]
    typer.echo("== 启用域 ==")
    line = ", ".join(enabled) if enabled else "(无)"
    if result["disabled"]:
        line += "  跳过: %s" % ", ".join(result["disabled"])
    typer.echo("  %s" % line)
    typer.echo("== 定时任务 ==")
    for task in result["installed"]:
        typer.echo("  ✓ %s  触发: %s" % (task["name"], ", ".join(task["times"])))
    for item in result["not_ready"]:
        typer.echo("  - %s(客户端未就绪,暂缓)" % item.get("name", item["domain"]))
    for item in result["failed"]:
        typer.echo("  ✗ %s: %s" % (item["name"], item["error"]))
    typer.echo("== Web 管理台 ==")
    typer.echo("  %s" % result["ui"])
    profiles = result.get("profiles", {})
    typer.echo("== 应用同步 ==")
    for item in profiles.get("synced", []):
        version = item.get("version") or "未知版本"
        typer.echo("  ✓ %-10s %s(%s)" % (item["domain"], version, item.get("region", "cn")))
    for item in profiles.get("failed", []):
        typer.echo("  - %s: %s" % (item.get("domain", "?"), item.get("error", "")))
    if result["todo"]:
        typer.echo("== 待办 ==")
        for line in result["todo"]:
            typer.echo("  • %s" % line)
    else:
        typer.echo("== 全部就绪 ==")
        typer.echo("  到触发时间自动执行;可用 auto-signin-kit <域名> doctor 复核登录态")
