"""定时调度端口: 定义"定时任务长什么样"与各平台必须提供的能力。

三种实现真实存在(Windows 任务计划程序 / macOS launchd / Linux cron),
所以这里才建端口;本模块只有形状,不 import 任何 adapter。

「未注册」是合法状态不是错误——``TaskStatus.installed=False`` 是正常的
查询结果,不要把它当失败抛异常,否则 ``status`` 命令在干净机器上会报错。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ScheduleSpec:
    """一个定时任务的完整定义;清单由域内 ``data`` 模块给出。

    ``argv`` 是完整的命令行,第一个元素是可执行文件名(不是绝对路径)——
    由 adapter 按平台解析成绝对路径,**域层不做这件事**(它属于 OS 差异)。
    """

    #: 任务标识;Windows 用任务名,macOS 拼进 label,Linux 用作 crontab 块标记。
    name: str
    #: 命令行(argv[0] 是可执行文件名,由 adapter 解析绝对路径)。
    argv: tuple[str, ...] = ()
    #: 本地时间 ``HH:MM``,一天内可多次触发。
    times: tuple[str, ...] = ()
    #: 单次执行的时限(分钟);平台不支持时限时忽略(见各 adapter 注释)。
    limit_minutes: int = 5
    #: 人类可读说明,写进任务描述里,便于在任务管理器里认出它。
    description: str = ""


@dataclass(frozen=True)
class TaskStatus:
    """单个任务的当前状态。"""

    name: str
    installed: bool
    #: 人类可读补充(平台细节/路径);空字符串表示无补充。
    detail: str = ""
    #: 实际写入的触发时间;平台读不出来时留空。
    times: tuple[str, ...] = field(default=())


class Scheduler(Protocol):
    """平台定时调度能力;三个实现见 ``core/adapters/scheduler/``。"""

    #: 平台标识(``windows`` / ``darwin`` / ``linux``),用于状态输出与诊断。
    platform: str

    def install(self, spec: ScheduleSpec) -> None:
        """注册任务;已存在则覆盖更新。失败抛 ``AdapterError`` 子类。"""

    def uninstall(self, name: str) -> None:
        """注销任务;原本就没注册时视为成功(幂等,不报错)。"""

    def status(self, name: str) -> TaskStatus:
        """查询单个任务是否已注册;不存在返回 ``installed=False``。"""

    def render(self, spec: ScheduleSpec) -> str:
        """渲染将要写入/执行的调度定义文本,**不落盘、不改动系统**。

        供 ``--dry-run`` 与人工核对使用: 让人在真注册前看到将要写什么。
        """
