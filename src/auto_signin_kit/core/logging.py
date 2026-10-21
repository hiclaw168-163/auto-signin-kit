"""统一日志输出: 全部走 stderr,保证命令替换(stdout)只含业务输出。

级别由环境变量 ``AUTO_SIGNIN_KIT_LOG_LEVEL`` 控制(DEBUG / INFO / WARNING / ERROR),
默认 INFO。

对齐 12-Factor App 的"日志即事件流"原则: 过程日志不写文件、不做自己的轮转,
业务数据(命令的真正输出)走 stdout,人看的过程信息走 stderr——这样
`eval "$(auto_signin_kit some-command)"` 这类命令替换场景才不会被日志污染。

结果 JSON 的**落盘文件**(silent/静默契约)是唯一的例外,也统一收口在这里:
``result_log_path()`` 把各域的日志文件定位到平台日志目录
(见 ``core/adapters/log_dir.py``),环境变量按域覆盖。
"""

import os
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

from auto_signin_kit.core.adapters.log_dir import default_log_dir
from auto_signin_kit.core.envvar import auto_signin_kit_env

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
_level = _LEVELS.get(auto_signin_kit_env("LOG_LEVEL") or "INFO", 20)
_console = Console(highlight=False, stderr=True, markup=False)


def set_level(name: str) -> None:
    """设置当前进程的日志级别;未知级别回退 INFO。"""
    global _level
    _level = _LEVELS.get(name.upper(), 20)


def _ts() -> str:
    return f"{datetime.now(tz=UTC).astimezone():%Y-%m-%d %H:%M:%S}"


def _emit(message: str, prefix: str, style: str) -> None:
    _console.print(f"{_ts()} {prefix}{message}", style=style)


def log_debug(message: str) -> None:
    """输出调试信息(仅 DEBUG 级别可见)。"""
    if _level <= 10:
        _emit(message, "  ", "dim")


def log_info(message: str) -> None:
    """输出常规进度信息。"""
    if _level <= 20:
        _emit(message, "▶ ", "cyan")


def log_warning(message: str) -> None:
    """输出警告信息(不中断主流程)。"""
    if _level <= 30:
        _emit(message, "⚠ ", "yellow")


def log_error(message: str) -> None:
    """输出错误信息(通常跟随非正常退出)。"""
    _emit(message, "✘ ", "red")


def log_success(message: str) -> None:
    """输出成功信息。"""
    _emit(message, "✓ ", "green")


def result_log_path(domain: str, *, env_var: str, filename: str) -> Path:
    """结果 JSON 落盘路径的统一入口(所有域共用,禁止各自再写一份)。

    优先级: ``env_var`` 指定的自定义路径 > 平台日志目录下的 ``filename``。
    Windows: ``%LOCALAPPDATA%\\auto-signin-kit\\logs``;macOS/Linux 见 log_dir 模块。
    """
    custom = os.environ.get(env_var)
    if custom:
        return Path(custom).expanduser()
    return default_log_dir() / filename
