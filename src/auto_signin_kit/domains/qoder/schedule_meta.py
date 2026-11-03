"""qoder 的调度声明: schedule 域经 entry-point 注册表发现,不反向 import 本域。

契约(:data:`SPECS` / :data:`DISPLAY_NAME` / :data:`ready`)见
workbuddy/schedule_meta.py 的模块注释。
"""

from __future__ import annotations

from auto_signin_kit.core.ports.scheduler import ScheduleSpec
from auto_signin_kit.domains.qoder.auth import client_info as _collect
from auto_signin_kit.domains.qoder.auth import is_installed

DISPLAY_NAME = "Qoder CN"

#: 域档案契约(可选声明): 客户端信息采集;UI 同步账户档案时调用。
client_info = _collect


def ready() -> bool:
    """客户端就绪探测: 本机装了 Qoder 客户端才允许注册调度。"""
    return is_installed()


SPECS: tuple[ScheduleSpec, ...] = (
    ScheduleSpec(
        name="QoderDailyCredits",
        argv=("auto-signin-kit", "qoder", "auto", "--silent"),
        times=("10:05", "11:05", "15:05", "19:05", "23:05"),
        limit_minutes=5,
        description="Qoder CN 每日 Credits 领取",
    ),
)
