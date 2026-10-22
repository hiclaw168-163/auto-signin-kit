"""history 域命令行入口: 台账查询(默认表格给人看,--json 给机器)与回填。"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from auto_signin_kit.core.config import load_domain_config_model
from auto_signin_kit.domains.history import service
from auto_signin_kit.domains.history.schema import HistoryDomainConfig

app = typer.Typer(help="签到台账: 规整地查看所有签到结果,不必翻日志")
console = Console()


def _config() -> HistoryDomainConfig:
    return load_domain_config_model("history", HistoryDomainConfig, merge_local=True)


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    days: int = typer.Option(None, "--days", "-d", help="回看最近几天(默认取配置)"),
    domain: str = typer.Option(None, "--domain", help="只看某个域(workbuddy/qoder/trae)"),
    date: str = typer.Option(None, "--date", help="只看某一天(YYYY-MM-DD)"),
    attention: bool = typer.Option(False, "--attention", help="只看需要人工处理的记录"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSONL 而非表格"),
) -> None:
    """默认动作 = 查台账: 每天/每域的最终签到状态一览。"""
    if ctx.invoked_subcommand is not None:
        return
    if date:
        rows = service.events(date=date, domain=domain, attention_only=attention)
    else:
        rows = service.daily_status(days or _config().default_days, domain=domain)
        if attention:
            rows = [row for row in rows if row["needs_attention"]]
    if json_output:
        for row in rows:
            console.print(json.dumps(row, ensure_ascii=False))
        return
    _print_status_table(rows, per_day=date is None)


@app.command()
def events(
    domain: str = typer.Option(None, "--domain", help="只看某个域"),
    date: str = typer.Option(None, "--date", help="只看某一天(YYYY-MM-DD)"),
    attention: bool = typer.Option(False, "--attention", help="只看需要人工处理的记录"),
    limit: int = typer.Option(50, "--limit", help="最多显示多少条"),
) -> None:
    """运行明细: 每次命令执行的原始结果(最新在前)。"""
    rows = service.events(date=date, domain=domain, attention_only=attention, limit=limit)
    table = Table(title="签到运行明细(最近 %d 条)" % len(rows))
    for column in ("时间", "域", "动作", "结果", "报告"):
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(
            row["ts"], row["domain"], row["action"] or "-",
            _mark(row["result"], row["needs_attention"]), row["report"] or "",
        )
    console.print(table)


@app.command("import-logs")
def import_logs() -> None:
    """把现有 JSONL 日志一次性回填进台账(幂等,重复导入自动去重)。"""
    result = service.import_logs()
    console.print(json.dumps(result, ensure_ascii=False))


@app.command("import-server")
def import_server() -> None:
    """把服务端还能查到的历史回填进台账(workbuddy 活跃日历 / qoder 领取记录)。"""
    result = service.server_backfill()
    console.print(json.dumps(result, ensure_ascii=False))


def _print_status_table(rows: list[dict], per_day: bool) -> None:
    title = "每日签到状态(每天每域取最后一次运行)" if per_day else "运行结果"
    table = Table(title=title)
    for column in ("日期", "域", "结果", "报告"):
        table.add_column(column, overflow="fold")
    for row in rows:
        day = row.get("day") or row["ts"][:10]
        table.add_row(
            day, row["domain"], _mark(row["result"], row["needs_attention"]),
            row["report"] or "",
        )
    console.print(table)


def _mark(result: str, needs_attention: bool) -> str:
    if needs_attention:
        return "[red]%s[/red]" % result
    if result in ("CLAIMED", "ACTIVE", "ALREADY", "OK", "GROWTH", "GROWN"):
        return "[green]%s[/green]" % result
    return result
