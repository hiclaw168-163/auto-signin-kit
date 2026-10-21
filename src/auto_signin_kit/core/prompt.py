"""交互 prompt 注入: service 层不 import typer,由 cli 层注入。

这是"decision 2: service/cli 分层"里一个容易被忽略的边界情况的解法——
service.py 不能 import typer,但有些业务逻辑确实需要交互确认(比如
"输出文件已存在,是否覆盖")。解法: cli 层用 ``set_prompt`` 把
``typer.prompt``/``typer.confirm`` 包装一下注入进来,service 层只调用
``prompt_str``/``prompt_confirm`` 这些跟 typer 无关的函数,不知道
背后到底是 typer 还是别的什么在真正问用户。

未注入时(比如跑单测,或者忘记注入)自动兜底成 ``builtins.input``,
不会因为没注入就崩溃。
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from typing import Any

_prompt_hook: contextvars.ContextVar[Callable[..., Any] | None] = contextvars.ContextVar(
    "auto_signin_kit_prompt_hook", default=None
)


def set_prompt(fn: Callable[..., Any] | None) -> None:
    """cli 层注入交互 prompt(签名: (prompt, default) -> str);传 None 清除。"""
    _prompt_hook.set(fn)


def get_prompt() -> Callable[..., Any]:
    """取当前 prompt;未注入时兜底 builtins.input。"""
    fn = _prompt_hook.get()
    if fn is not None:
        return fn

    def _fallback(prompt: str, default: str) -> str:
        raw = input(f"{prompt} [{default}]: ")
        return raw.strip() or default

    return _fallback


def prompt_str(message: str, default: str = "") -> str:
    """读一行字符串;走 ``get_prompt``(cli 注入或 input 兜底)。"""
    raw = get_prompt()(message, default)
    if raw is None:
        return default
    text = str(raw).strip()
    return text if text else default


def prompt_confirm(message: str, default: bool = True) -> bool:
    """读 y/n 确认;走 ``get_prompt``。空输入返回 default。"""
    raw = prompt_str(message, "y" if default else "n").lower()
    if raw in {"y", "yes"}:
        return True
    if raw in {"n", "no"}:
        return False
    return default


# 项目自己需要的交互确认,照这个模式加,比如:
#
# def confirm_overwrite(path: Path, yes: bool) -> None:
#     """输出文件已存在且未 --yes 时交互确认;取消则抛 CommandError。"""
#     msg = f"输出已存在: {path}, 是否覆盖?"
#     if path.exists() and not yes and not prompt_confirm(msg, default=False):
#         raise CommandError("已取消,未覆盖输出文件")
