"""doubao 的调度声明: schedule 域经 entry-point 注册表发现,不反向 import 本域。

契约(:data:`SPECS` / :data:`DISPLAY_NAME` / :data:`ready`)见
workbuddy/schedule_meta.py 的模块注释。
"""

from __future__ import annotations

from auto_signin_kit.core.ports.scheduler import ScheduleSpec
from auto_signin_kit.domains.doubao.client_info import client_info as _collect

DISPLAY_NAME = "豆包"

#: 无客户端就绪探测: doubao 走 CDP 驱动浏览器,浏览器不在运行时由域自身
#: 如实报 NO_AUTH,注册不做前置拦截。
ready = None

#: 域档案契约(可选声明): 客户端信息采集;UI 同步账户档案时调用。
client_info = _collect

SPECS: tuple[ScheduleSpec, ...] = (
    ScheduleSpec(
        name="DoubaoDailyCheckin",
        argv=("auto-signin-kit", "doubao", "auto", "--silent"),
        times=("00:05", "09:05", "13:05", "19:05", "23:05"),
        limit_minutes=5,
        description="豆包每日签到 + 任务奖励清扫(签到后自动领取已完成任务奖励)",
    ),
)
