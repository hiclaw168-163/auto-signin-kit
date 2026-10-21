"""编排步骤契约: 顺序 run,失败时按完成序逆序 rollback。

跟 auto_signin_kit 里任何具体业务完全无关,新项目原样复制即可,不需要改一行。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class StepResult:
    """单个 Step 的执行结果。"""

    ok: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class Step(Protocol):
    """编排步骤契约: 顺序 run,失败时按完成序逆序 rollback。"""

    name: str

    def run(self, ctx: dict[str, Any]) -> StepResult:
        """执行本步骤;失败可抛异常或返回 ``ok=False``。"""
        ...

    def rollback(self, ctx: dict[str, Any]) -> None:
        """回滚本步骤(在后续步骤失败时由 Pipeline 逆序调用)。

        不是所有 Step 都需要覆盖这个方法——只有真正安全、可逆的操作才应该
        注册 rollback。有远程副作用(已推送、已触发外部系统)的步骤失败时,
        应该显式中止并提示人工介入,而不是自动做一些"看起来是撤销但实际
        可能引入新问题"的操作。默认不做任何事。
        """


class CallableStep:
    """把一个既有函数包装成 Step,用于把"改造前就能跑"的函数接入 Pipeline,
    不用为了套 Pipeline 而重写所有业务函数。

    如果你的 Step 是从零写的纯函数,不需要这个包装类,直接实现 Step 协议即可。
    这个类是给"手头已经有一批能跑的函数,想让它们参与统一编排/回滚"这种
    迁移场景用的。
    """

    def __init__(self, name: str, func, rollback_func=None, **kwargs: Any) -> None:
        """name 用于日志与回滚定位;func 为该步动作;rollback_func 可选。"""
        self.name = name
        self._func = func
        self._rollback_func = rollback_func
        self._kwargs = kwargs

    def run(self, ctx: dict[str, Any]) -> StepResult:
        """调用包装函数;成功返回 ok=True。

        仅把构造时的显式 kwargs 传给 func;需要读 Pipeline 上下文的步骤
        应自行从 ctx 取值,避免函数签名被迫接受任意 ctx 字段。
        """
        result = self._func(**self._kwargs)
        return StepResult(ok=True, data={"result": result})

    def rollback(self, ctx: dict[str, Any]) -> None:
        """若构造时提供了 rollback 回调则调用。"""
        if self._rollback_func is not None:
            self._rollback_func(**self._kwargs)
