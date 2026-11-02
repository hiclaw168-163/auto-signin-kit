"""workbuddy 的调度声明: schedule 域经 entry-point 注册表发现,不反向 import 本域。

契约(:data:`SPECS` / :data:`DISPLAY_NAME` / :data:`ready`):
- ``SPECS``: 本域要注册的定时任务;触发时间与执行时限的默认值在这里,
  可被 ``config/domains/schedule.yaml`` 覆盖(见 schedule 域 data.apply_overrides)。
- ``DISPLAY_NAME``: 客户端展示名(探测失败提示用)。
- ``ready``: 客户端就绪探测;``None`` 表示无探测(照常注册,不挡路)。

时间预算约束见 schedule/data.py 模块注释(高密度触发 + 幂等领取)。
"""

from __future__ import annotations

from auto_signin_kit.core.ports.scheduler import ScheduleSpec
from auto_signin_kit.domains.workbuddy.runtime import is_installed

DISPLAY_NAME = "WorkBuddy"


def ready() -> bool:
    """客户端就绪探测: 本机装了 WorkBuddy 客户端才允许注册调度。"""
    return is_installed()


SPECS: tuple[ScheduleSpec, ...] = (
    ScheduleSpec(
        name="WorkBuddyAutoSignin",
        argv=("auto-signin-kit", "workbuddy", "auto", "--silent"),
        times=("00:05",),
        limit_minutes=10,
        description="WorkBuddy 每日签到 + 成长中心(每日主入口)",
    ),
    ScheduleSpec(
        name="WorkBuddyGrowthPoll",
        argv=("auto-signin-kit", "workbuddy", "poll"),
        times=("01:05", "09:05", "17:05", "21:05"),
        limit_minutes=5,
        description="WorkBuddy 成长中心轮询(每次先查签到,兼做主入口的兜底)",
    ),
    ScheduleSpec(
        name="WorkBuddyActiveCheck",
        argv=("auto-signin-kit", "workbuddy", "active", "--silent"),
        times=("12:05", "20:05"),
        limit_minutes=5,
        description="WorkBuddy 活跃保底: 查今日活跃,没有就自动补一次对话(活跃天数只认对话)",
    ),
)
