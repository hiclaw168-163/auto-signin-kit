"""schedule 域业务逻辑: 注册 / 注销 / 查询各域的定时任务。

OS 差异全部收口在 ``core/adapters/scheduler``(STD-05),本层只见
``Scheduler`` 协议——不出现 schtasks / launchctl / crontab,也不出现平台分支。

``scheduler`` 参数用于测试注入;不传时惰性取工厂(架构门禁禁止 domains 直接
subprocess,也不要求 cli 层反向 import 适配器)。
"""

from __future__ import annotations

from typing import Any

from auto_signin_kit.core.adapters.scheduler import get_scheduler
from auto_signin_kit.core.errors import AdapterError
from auto_signin_kit.core.ports.scheduler import Scheduler, ScheduleSpec
from auto_signin_kit.core.report import emit_result
from auto_signin_kit.domains.schedule.data import apply_overrides, known_domains, tasks_for

#: 可用动作;``render`` 由 cli 层单独走 :func:`render_text`(它输出的是文本而非 JSON)。
ACTIONS = ("install", "uninstall", "status")


def select_tasks(config: Any, domain: str | None) -> tuple[ScheduleSpec, ...]:
    """按 ``--domain`` 与配置里的 ``enabled_tasks`` 求交集,再套用配置覆盖。

    顺序不能反: 先筛选再覆盖——否则给未启用任务写的覆盖项也会被解析,时间格式
    错误会在"根本没打算注册"的任务上炸出来。
    """
    pool = tasks_for(domain)
    enabled = [name for name in getattr(config, "enabled_tasks", None) or [] if name]
    if enabled:
        allowed = set(enabled)
        pool = tuple(spec for spec in pool if spec.name in allowed)
    overrides = getattr(config, "tasks", None) or {}
    return tuple(apply_overrides(spec, overrides.get(spec.name)) for spec in pool)


def install(
    tasks: tuple[ScheduleSpec, ...],
    scheduler: Scheduler,
    *,
    dry_run: bool = False,
) -> tuple[list[dict], list[dict]]:
    """逐个注册;**单个任务失败不中断其余**,失败项带 name 一并返回。

    不中断是有意的: 一个域注册失败(比如还没装那个助手)不该连累另外三个域,
    用户能看到"哪些成了、哪些没成"比看到第一条错误就中止更有用。
    """
    done: list[dict] = []
    failed: list[dict] = []
    for spec in tasks:
        if dry_run:
            done.append({"name": spec.name, "times": list(spec.times)})
            continue
        try:
            scheduler.install(spec)
        except AdapterError as exc:
            failed.append({"name": spec.name, "error": str(exc)})
        else:
            done.append({"name": spec.name, "times": list(spec.times)})
    return done, failed


def uninstall(
    tasks: tuple[ScheduleSpec, ...], scheduler: Scheduler, *, dry_run: bool = False
) -> tuple[list[dict], list[dict]]:
    """逐个注销;幂等(原本没注册也算成功)。"""
    done: list[dict] = []
    failed: list[dict] = []
    for spec in tasks:
        if dry_run:
            done.append({"name": spec.name})
            continue
        try:
            scheduler.uninstall(spec.name)
        except AdapterError as exc:
            failed.append({"name": spec.name, "error": str(exc)})
        else:
            done.append({"name": spec.name})
    return done, failed


def status(tasks: tuple[ScheduleSpec, ...], scheduler: Scheduler) -> list[dict]:
    """查询各任务是否已注册;"未注册"是正常状态,不是失败。"""
    out: list[dict] = []
    for spec in tasks:
        current = scheduler.status(spec.name)
        item = {
            "name": spec.name,
            "installed": current.installed,
            "times": list(spec.times),
        }
        if current.detail:
            item["detail"] = current.detail
        out.append(item)
    return out


def render_text(tasks: tuple[ScheduleSpec, ...], scheduler: Scheduler) -> str:
    """渲染各任务将要写入的调度定义;**不落盘、不改动系统**,只给人看。"""
    parts = []
    for spec in tasks:
        parts.append(f"# {spec.name} · platform={scheduler.platform}\n{scheduler.render(spec)}")
    return "\n".join(parts)


def render_definitions(
    config: Any, domain: str | None = None, *, scheduler: Scheduler | None = None
) -> str:
    """cli 层的 ``render`` 命令入口: 本层取调度器,cli 不必反向 import 适配器。"""
    active = scheduler if scheduler is not None else get_scheduler()
    return render_text(select_tasks(config, domain), active)


def _summary(action: str, done: list[dict], failed: list[dict]) -> dict:
    """把 install/uninstall 的结果组装成一行 JSON 契约。"""
    total = len(done) + len(failed)
    if failed:
        result = "PARTIAL" if done else "FAILED"
        report = "%s: %d/%d 成功,失败 %s" % (
            action, len(done), total, "、".join(item["name"] for item in failed)
        )
    else:
        result = "OK"
        report = "%s: %d 个任务已就绪" % (action, total)
    out = {
        "result": result,
        "needs_attention": bool(failed),
        "action": action,
        "done": done,
        "failed": failed,
        "report": report,
    }
    return out


def run_action(
    action: str,
    config: Any,
    *,
    domain: str | None = None,
    dry_run: bool = False,
    scheduler: Scheduler | None = None,
) -> int:
    """命令分发;结果按一行 JSON 契约输出,退出码 0/1/2,本函数保证不抛业务异常。"""
    if action not in ACTIONS:
        emit_result({
            "result": "ERROR",
            "needs_attention": True,
            "report": "未知命令：%s（可用：%s）" % (action, " / ".join(ACTIONS)),
        })
        return 2
    tasks = select_tasks(config, domain)
    if not tasks:
        emit_result({
            "result": "ERROR",
            "needs_attention": True,
            "report": "没有匹配的任务（可用域：%s；也检查配置里的 enabled_tasks）"
                      % "、".join(known_domains()),
        })
        return 2

    active = scheduler if scheduler is not None else get_scheduler()
    if action == "status":
        items = status(tasks, active)
        missing = [item["name"] for item in items if not item["installed"]]
        emit_result({
            "result": "OK",
            "needs_attention": False,
            "platform": active.platform,
            "tasks": items,
            "report": "已注册 %d/%d%s"
                      % (len(items) - len(missing), len(items),
                         "" if not missing else "，未注册：" + "、".join(missing)),
        })
        return 0

    done, failed = (
        install(tasks, active, dry_run=dry_run)
        if action == "install"
        else uninstall(tasks, active, dry_run=dry_run)
    )
    out = _summary("DRY_RUN" if (dry_run and action == "install") else action, done, failed)
    out["platform"] = active.platform
    if dry_run:
        out["report"] = "[dry-run] " + out["report"]
    emit_result(out)
    return 1 if failed else 0
