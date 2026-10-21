"""schedule 域配置,对应 ``config/domains/schedule.yaml``。

触发时间与执行时限在这里配置(可调),命令行不在这里配(它是代码结构,
不是可调参数)。时限与各域的时间预算联动,改之前先看
``domains/schedule/data.py`` 模块文档里的约束。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from auto_signin_kit.core.config.schema import DomainConfig


class TaskOverride(BaseModel):
    """单个任务的调度覆盖项;没给的字段沿用 ``data.py`` 清单里的默认值。"""

    #: 触发时间 ``HH:MM``,一天内可多个;空列表 = 沿用清单默认值。
    times: list[str] = Field(default_factory=list)
    #: 执行时限(分钟);``null`` = 沿用清单默认值。
    limit_minutes: int | None = None


class ScheduleDomainConfig(DomainConfig):
    """schedule 域配置项;``extra="forbid"``,未知字段直接报错。"""

    #: 只注册清单里的这些任务(按 ``name``);空列表 = 全部注册。
    #: 填了清单里不存在的名字会得到空任务集,由 service 明确报错。
    enabled_tasks: list[str] = Field(default_factory=list)
    #: 按任务名覆盖触发时间/时限;未列出的任务沿用清单默认值,所以只想改
    #: 某一个任务时,不必把整张表抄一遍。
    tasks: dict[str, TaskOverride] = Field(default_factory=dict)
