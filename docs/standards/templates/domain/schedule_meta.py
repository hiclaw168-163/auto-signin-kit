"""<域名> 的调度声明: schedule 域经 entry-point 注册表发现,不反向 import 本域。

契约(三个模块级属性):
- ``SPECS``: 本域要注册的定时任务(:class:`ScheduleSpec` 元组)。触发时间与
  执行时限的默认值在这里,可被 ``config/domains/schedule.yaml`` 覆盖。
- ``DISPLAY_NAME``: 客户端展示名(探测失败提示用)。
- ``ready``: 客户端就绪探测(返回 bool);无探测需求时声明 ``ready = None``。

设计约束见 ``domains/schedule/data.py`` 模块注释(高密度触发 + 幂等领取;
执行时限必须大于域自身时间预算)。

注册: ``pyproject.toml`` 的 ``[project.entry-points."auto_signin_kit.schedule_meta"]``
加一行 ``<域名> = "auto_signin_kit.domains.<域名>.schedule_meta"``。
"""

from __future__ import annotations

from auto_signin_kit.core.ports.scheduler import ScheduleSpec

DISPLAY_NAME = "<展示名>"


def ready() -> bool:
    """客户端就绪探测;无探测需求时删除本函数并在模块级声明 ``ready = None``。"""
    return True


SPECS: tuple[ScheduleSpec, ...] = (
    ScheduleSpec(
        name="<TaskName>",
        argv=("auto-signin-kit", "<域名>", "auto", "--silent"),
        times=("09:05",),  # 高密度触发兜底,见 schedule/data.py 注释,别"优化"掉
        limit_minutes=5,
        description="<任务说明>",
    ),
)
