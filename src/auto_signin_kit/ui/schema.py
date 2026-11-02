"""ui 层的请求/响应模型: 只描述传输形状,校验交给 pydantic,业务语义在路由层。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ScheduleOp(BaseModel):
    """调度 install/uninstall 的请求体;``domains`` 缺省 = 全部。"""

    domains: list[str] | None = Field(
        None, description="只操作这些域(如 [\"doubao\"]);缺省=全部",
    )


class JobOut(BaseModel):
    """一次手动触发的对外视图(与 runner.Job.public() 对齐)。"""

    id: str
    domain: str
    action: str
    argv: list[str]
    started_at: str
    finished_at: str | None
    status: str
    exit_code: int | None
    output: str
    result: dict[str, Any] | None
