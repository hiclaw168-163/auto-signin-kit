"""Pipeline 执行器: 顺序执行 Step,任一失败即停并逆序回滚已完成步骤。

跟具体业务无关,新项目原样复制、替换 auto_signin_kit 前缀即可。
"""
from __future__ import annotations

from typing import Any

from auto_signin_kit.core.errors import AutoSignInKitError, CommandError
from auto_signin_kit.core.logging import log_info, log_success, log_warning
from auto_signin_kit.orchestration.step import Step, StepResult


class PipelineError(CommandError):
    """流水线执行失败;携带失败步骤名与已完成步骤,方便上层打印诊断信息。"""

    def __init__(self, message: str, *, failed_step: str, completed: list[str]) -> None:
        """failed_step 是失败步骤名;completed 是已完成(待回滚)步骤名列表。"""
        super().__init__(message)
        self.failed_step = failed_step
        self.completed = completed


class Pipeline:
    """顺序执行步骤;任一步失败则逆序 rollback 已完成步骤并抛 PipelineError。"""

    def __init__(self, steps: list[Step]) -> None:
        """steps: 有序步骤列表,执行时按序调用。"""
        self.steps = steps

    def run(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """执行全部步骤,返回上下文;失败抛 PipelineError。"""
        ctx = context if context is not None else {}
        completed: list[Step] = []
        for step in self.steps:
            log_info(f"步骤: {step.name}")
            try:
                result = step.run(ctx)
            except PipelineError:
                raise
            except AutoSignInKitError:
                # 业务异常原样上抛(ConfigError/自定义异常等退出码语义不变);先回滚已完成步骤
                self._rollback(completed, ctx)
                raise
            except SystemExit as e:
                # 如果某个 Step 是用 CallableStep 包装的既有 typer 命令函数,
                # 那个函数内部可能会 raise typer.Exit(...),它在解释器层面就是 SystemExit。
                # 如果你的 Step 都是从零写的纯函数、不会抛 SystemExit,这个分支永远不会触发,
                # 留着无害,不需要删。
                if e.code in (0, None):
                    result = StepResult(ok=True)
                else:
                    self._rollback(completed, ctx)
                    raise PipelineError(
                        f"{step.name}失败",
                        failed_step=step.name,
                        completed=[s.name for s in completed],
                    ) from e
            except Exception as e:
                self._rollback(completed, ctx)
                raise PipelineError(
                    f"{step.name}失败: {e}",
                    failed_step=step.name,
                    completed=[s.name for s in completed],
                ) from e

            if not result.ok:
                self._rollback(completed, ctx)
                raise PipelineError(
                    f"{step.name}失败: {result.message}",
                    failed_step=step.name,
                    completed=[s.name for s in completed],
                )
            log_success(f"{step.name}成功")
            completed.append(step)
        return ctx

    def _rollback(self, completed: list[Step], ctx: dict[str, Any]) -> None:
        for step in reversed(completed):
            try:
                step.rollback(ctx)
            except Exception as e:  # noqa: BLE001 — 回滚尽最大努力,不掩盖原失败
                log_warning(f"回滚 {step.name} 时出错(已忽略): {e}")
