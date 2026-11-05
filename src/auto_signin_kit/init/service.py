"""init 一键初始化的业务逻辑(纯 Python,禁 typer;交互经 core.prompt 注入)。

复用而不是复制: 调度注册走 schedule.service(任务发现/就绪探测/幂等注册),
管理台守护走 ui.daemon,域清单来自 schedule_meta 注册表——init 只做编排与
汇总,不复制任何域知识。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from auto_signin_kit.core.config import load_domain_config_model
from auto_signin_kit.core.errors import ConfigError
from auto_signin_kit.core.report import emit_result
from auto_signin_kit.domains.schedule.schema import ScheduleDomainConfig
from auto_signin_kit.domains.schedule.service import (
    _default_probe,
    filter_ready,
    install,
    select_tasks,
)

#: 初始化动作的调用注入点(测试替换用)。
PromptConfirm = Callable[[str, bool], bool]


def business_domains() -> list[dict[str, str]]:
    """可参与每日调度的业务域(= 有 schedule_meta 声明的域),含展示名。"""
    from auto_signin_kit.domains.schedule.data import providers

    return [
        {"domain": name, "display": getattr(module, "DISPLAY_NAME", name)}
        for name, module in providers()
    ]


def env_checks() -> list[dict[str, Any]]:
    """硬性环境检查;``block=True`` 的项失败则整个 init 中止。"""
    from auto_signin_kit.core import store

    checks: list[dict[str, Any]] = []
    domains = business_domains()
    checks.append({
        "name": "业务域注册表",
        "ok": bool(domains),
        "block": True,
        "detail": "发现 %d 个域: %s" % (len(domains), ", ".join(d["domain"] for d in domains))
        if domains else "为空,请执行 uv sync && uv tool install --editable '.[web]' --force",
    })
    data_dir = store.db_path().parent
    writable = data_dir.exists() and os.access(data_dir, os.W_OK)
    checks.append({
        "name": "数据目录可写",
        "ok": writable,
        "block": True,
        "detail": str(data_dir),
    })
    try:
        load_domain_config_model("schedule", ScheduleDomainConfig, merge_local=True)
        checks.append({"name": "调度配置", "ok": True, "block": True, "detail": "加载成功"})
    except ConfigError as exc:
        checks.append({"name": "调度配置", "ok": False, "block": True, "detail": str(exc)})
    return checks


def _pick_enabled(
    domains: list[dict[str, str]], interactive: bool, confirm: PromptConfirm
) -> tuple[list[dict[str, str]], list[str]]:
    """逐域询问是否启用;非交互模式默认全启用(注册是幂等的,多启用无害)。"""
    enabled: list[dict[str, str]] = []
    disabled: list[str] = []
    for item in domains:
        if interactive and not confirm(
            "启用 %s(%s)的每日自动任务?" % (item["display"], item["domain"]), True,
        ):
            disabled.append(item["domain"])
        else:
            enabled.append(item)
    return enabled, disabled


def _install_schedule(
    enabled: list[dict[str, str]],
) -> tuple[list[dict], list[dict], list[dict]]:
    """对启用域逐个注册调度;返回(完成, 失败, 未就绪)。"""
    config = load_domain_config_model("schedule", ScheduleDomainConfig, merge_local=True)
    done: list[dict] = []
    failed: list[dict] = []
    not_ready: list[dict] = []
    for item in enabled:
        tasks = select_tasks(config, item["domain"])
        ready, blocked = filter_ready(tasks, _default_probe)
        not_ready.extend({"domain": item["domain"], **task} for task in blocked)
        ok, bad = install(ready, _get_scheduler())
        done.extend(ok)
        failed.extend(bad)
    return done, failed, not_ready


def _get_scheduler() -> Any:
    # 惰性导入: 与 schedule 域同理,架构门禁禁止直接 import subprocess 实现
    from auto_signin_kit.core.adapters.scheduler import get_scheduler

    return get_scheduler()


def _sync_profiles() -> dict[str, Any]:
    """应用同步(落 ask_apps 主档 + ask_accounts 档案): 复用管理台的采集编排。

    逻辑本体在 ui.apps_api(遍历注册表的 client_info/ready 声明),init 在顶层
    编排它属于允许的依赖方向(init > ui);失败不挡初始化,如实上报。
    """
    try:
        from auto_signin_kit.ui.apps_api import sync_apps

        return sync_apps()
    except Exception as exc:  # 档案同步失败不影响就绪判定
        return {"result": "ERROR", "synced": [], "failed": [{"error": str(exc)}]}


def _ensure_ui(start_ui: bool, interactive: bool, confirm: PromptConfirm) -> str:
    """确保管理台守护进程在跑;返回状态描述(不因 UI 失败而整体失败)。"""
    if not start_ui:
        return "skipped(--no-ui)"
    if interactive and not confirm("启动 Web 管理台(后台守护,http://127.0.0.1:8688)?", True):
        return "skipped(用户跳过)"
    from auto_signin_kit.ui import daemon as ui_daemon

    state, _ = ui_daemon.inspect()
    if state == "running":
        return "已在运行(http://127.0.0.1:8688)"
    try:
        ui_daemon.start_daemon("127.0.0.1", 8688, open_browser=False)
    except Exception as exc:  # UI 起不来不挡初始化,如实上报即可
        return "启动失败: %s(可稍后执行 auto-signin-kit ui serve --daemon)" % exc
    return "已启动(http://127.0.0.1:8688)"


def run_init(
    interactive: bool,
    start_ui: bool = True,
    *,
    confirm: PromptConfirm | None = None,
) -> dict[str, Any]:
    """执行初始化,返回供 CLI 渲染的结构化结果。

    任何一步失败都不中断后续步骤——用户需要看到"哪些成了、哪些还差什么",
    而不是第一条错误就黑屏;整体退出码由 CLI 按结果计算。
    """
    confirm_fn = confirm or _default_confirm
    checks = env_checks()
    hard_fail = [c for c in checks if not c["ok"] and c["block"]]

    enabled: list[dict[str, str]] = []
    disabled: list[str] = []
    done: list[dict] = []
    failed: list[dict] = []
    not_ready: list[dict] = []
    if not hard_fail:
        enabled, disabled = _pick_enabled(business_domains(), interactive, confirm_fn)
        if enabled:
            done, failed, not_ready = _install_schedule(enabled)

    ui_state = (
        _ensure_ui(start_ui, interactive, confirm_fn)
        if not hard_fail else "skipped(环境未就绪)"
    )

    profiles: dict[str, Any] = {"result": "SKIPPED", "synced": [], "failed": []}
    if not hard_fail and enabled:
        profiles = _sync_profiles()

    todo = _todo_list(hard_fail, not_ready, failed)
    result: dict[str, Any] = {
        "checks": checks,
        "enabled": [d["domain"] for d in enabled],
        "disabled": disabled,
        "installed": done,
        "failed": failed,
        "not_ready": not_ready,
        "ui": ui_state,
        "profiles": profiles,
        "todo": todo,
        "exit": 2 if hard_fail else (1 if todo else 0),
    }
    emit_result(
        {
            "result": "OK" if not todo else ("PARTIAL" if done else "FAILED"),
            "report": "初始化完成: %d 个任务注册, %d 项待办" % (len(done), len(todo)),
        },
        domain="schedule",
        action="init",
        silent=True,
        request={"interactive": interactive, "enabled": result["enabled"]},
    )
    return result


def _default_confirm(message: str, default: bool) -> bool:
    from auto_signin_kit.core.prompt import prompt_confirm

    return prompt_confirm(message, default)


def _todo_list(
    hard_fail: list[dict], not_ready: list[dict], failed: list[dict]
) -> list[str]:
    """把"还差什么"汇总成人话清单;空清单 = 一切就绪。"""
    todo: list[str] = []
    for check in hard_fail:
        todo.append("修复环境问题[%s]: %s" % (check["name"], check.get("detail", "")))
    for item in not_ready:
        todo.append(
            "登录 %s 客户端后执行: auto-signin-kit schedule install --domain %s"
            % (item.get("error", item["domain"]).split(" ")[0], item["domain"])
        )
    for item in failed:
        todo.append("注册失败[%s]: %s" % (item["name"], item["error"]))
    return todo
