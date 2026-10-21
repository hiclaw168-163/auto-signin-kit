"""定时调度适配器的工厂: 按当前 OS 选实现。

判断当前 OS 属于框架职责(STD-05),业务域里不允许出现平台分支——
域只拿一个 ``Scheduler`` 用,不关心背后是 schtasks 还是 crontab。
"""

from __future__ import annotations

from auto_signin_kit.core.adapters.platform_info import IS_MACOS, IS_WINDOWS
from auto_signin_kit.core.adapters.scheduler.common import SchedulerError  # noqa: F401
from auto_signin_kit.core.ports.scheduler import Scheduler

__all__ = ["SchedulerError", "get_scheduler"]


def get_scheduler() -> Scheduler:
    """返回当前平台的调度器实现。

    Windows → 任务计划程序;macOS → launchd;其余(含各种 Linux 发行版
    与 BSD)→ crontab。三者的能力差异(时限支持等)写在各自模块头部。
    """
    if IS_WINDOWS:
        from auto_signin_kit.core.adapters.scheduler.windows import WindowsScheduler

        return WindowsScheduler()
    if IS_MACOS:
        from auto_signin_kit.core.adapters.scheduler.darwin import DarwinScheduler

        return DarwinScheduler()
    from auto_signin_kit.core.adapters.scheduler.linux import LinuxScheduler

    return LinuxScheduler()
