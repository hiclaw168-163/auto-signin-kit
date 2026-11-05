"""workbuddy 的调度声明: schedule 域经 entry-point 注册表发现,不反向 import 本域。

契约(:data:`SPECS` / :data:`DISPLAY_NAME` / :data:`ready`):
- ``SPECS``: 本域要注册的定时任务;触发时间与执行时限的默认值在这里,
  可被 ``config/domains/schedule.yaml`` 覆盖(见 schedule 域 data.apply_overrides)。
- ``DISPLAY_NAME``: 客户端展示名(探测失败提示用)。
- ``ready``: 客户端就绪探测;``None`` 表示无探测(照常注册,不挡路)。
- ``accounts``(SPEC-12,可选): 返回声明且启用的**非 default** 账户名——
  schedule 按它把每个任务展开成"基础实例(default)+ 每账户实例(@名字,错峰)"。

时间预算约束见 schedule/data.py 模块注释(高密度触发 + 幂等领取)。
"""

from __future__ import annotations

from auto_signin_kit.core.config import load_domain_config_model
from auto_signin_kit.core.ports.scheduler import ScheduleSpec
from auto_signin_kit.domains.workbuddy.runtime import client_info as _collect
from auto_signin_kit.domains.workbuddy.runtime import is_installed
from auto_signin_kit.domains.workbuddy.schema import WorkbuddyDomainConfig

DISPLAY_NAME = "WorkBuddy"
#: 域类别(web=网页版签 / client=客户端签到);UI 类别列与同步档案用。
KIND = "client"
#: 应用描述(同步时自动补 ask_apps.app_desc,只补空)。
DESCRIPTION = "WorkBuddy 客户端每日签到与成长任务"

#: 域档案契约(可选声明): 客户端信息采集;UI 同步账户档案时调用。
client_info = _collect


def accounts() -> tuple[str, ...]:
    """声明且启用的具名账户(default 骑基础声明,不在此列)。"""
    config = load_domain_config_model("workbuddy", WorkbuddyDomainConfig, merge_local=True)
    return tuple(ref.name for ref in config.accounts
                 if ref.name != "default" and ref.enabled)


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
