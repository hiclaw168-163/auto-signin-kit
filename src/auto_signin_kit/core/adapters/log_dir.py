"""每用户数据目录定位: 可写日志/缓存目录的 OS 差异只在本模块。

Windows 用 ``%LOCALAPPDATA%``,macOS 遵循 ``~/Library/Logs``,
Linux 遵循 XDG State 规范;目录**惰性创建**,import 本模块无副作用。
"""

from __future__ import annotations

import os
from pathlib import Path

from auto_signin_kit.core.adapters.platform_info import IS_MACOS, IS_WINDOWS

APP_DIR_NAME = "auto-signin-kit"  # 每用户数据树的实际目录名


def app_data_root() -> Path:
    """每用户数据根(自动创建);OS 差异收口在本函数。"""
    home = Path.home()
    roots: list[Path] = []
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots.append(Path(local))
        roots.append(home / "AppData" / "Local")
    elif IS_MACOS:
        roots.append(home / "Library" / "Application Support")
    else:  # IS_LINUX 及其余 unix: XDG State 规范,缺省 ~/.local/state
        state = os.environ.get("XDG_STATE_HOME")
        if state:
            roots.append(Path(state).expanduser())
        roots.append(home / ".local" / "state")
    for root in roots:
        if root.exists():
            break
    else:
        root = roots[-1]
    data_root = root / APP_DIR_NAME
    data_root.mkdir(parents=True, exist_ok=True)
    return data_root


def default_log_dir() -> Path:
    """结果日志目录(自动创建)。

    Windows: ``%LOCALAPPDATA%\\auto-signin-kit\\logs``;
    macOS: ``~/Library/Logs/auto-signin-kit``(遵循 Apple 日志惯例);
    Linux: ``$XDG_STATE_HOME/auto-signin-kit/log``,缺省 ``~/.local/state/...``。
    """
    home = Path.home()
    if IS_WINDOWS:
        parent = app_data_root() / "logs"
    elif IS_MACOS:
        parent = home / "Library" / "Logs" / APP_DIR_NAME
    else:
        parent = app_data_root() / "log"
    parent.mkdir(parents=True, exist_ok=True)
    return parent
