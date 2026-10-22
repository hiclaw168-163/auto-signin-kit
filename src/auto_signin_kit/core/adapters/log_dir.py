"""结果日志目录定位: 可写日志目录的差异收口在本模块。

优先级: ``AUTO_SIGNIN_KIT_LOG_DIR`` 环境变量 > 项目 ``logs/`` 目录(从包位置
向上找 pyproject.toml) > 平台惯例目录(Windows ``%LOCALAPPDATA%``、macOS
``~/Library/Logs``、Linux XDG State);目录**惰性创建**,import 本模块无副作用。
"""

from __future__ import annotations

import os
from pathlib import Path

from auto_signin_kit.core.adapters.platform_info import IS_MACOS, IS_WINDOWS
from auto_signin_kit.core.envvar import auto_signin_kit_env

APP_DIR_NAME = "auto-signin-kit"  # 每用户数据树的实际目录名
_LOG_DIR_ENV = "LOG_DIR"  # 实际环境变量名: AUTO_SIGNIN_KIT_LOG_DIR
_DATA_DIR_ENV = "DATA_DIR"  # 实际环境变量名: AUTO_SIGNIN_KIT_DATA_DIR
# 向上找 pyproject.toml 的层数上限,防止在高处撞见无关项目的同名文件
_MAX_PARENT_WALK = 6


def project_root() -> Path | None:
    """从包自身位置向上定位项目根(找到 pyproject.toml 的最近一层)。

    锚点是**包的位置**而不是 cwd: 定时任务平台的 cwd 不可控,而
    ``uv tool install --editable`` 的包文件就躺在仓库里,位置永远可靠。
    非 editable 安装(包进了 site-packages)返回 None。
    """
    cur = Path(__file__).resolve().parent
    for _ in range(_MAX_PARENT_WALK):
        if (cur / "pyproject.toml").is_file():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


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
    """结果日志目录(自动创建),优先级从高到低:

    1. ``AUTO_SIGNIN_KIT_LOG_DIR`` 环境变量——显式配置,跨平台行为一致;
    2. 项目的 ``logs/`` 目录——从包位置向上定位 pyproject.toml
       (editable 安装时即仓库根),查看日志不用翻系统目录;
    3. 平台惯例目录——非项目环境兜底:
       Windows ``%LOCALAPPDATA%\\auto-signin-kit\\logs``;
       macOS ``~/Library/Logs/auto-signin-kit``(遵循 Apple 日志惯例);
       Linux ``$XDG_STATE_HOME/auto-signin-kit/log``,缺省 ``~/.local/state/...``。
    """
    explicit = auto_signin_kit_env(_LOG_DIR_ENV)
    if explicit:
        parent = Path(explicit).expanduser()
    else:
        root = project_root()
        if root is not None:
            parent = root / "logs"
        else:
            home = Path.home()
            if IS_WINDOWS:
                parent = app_data_root() / "logs"
            elif IS_MACOS:
                parent = home / "Library" / "Logs" / APP_DIR_NAME
            else:
                parent = app_data_root() / "log"
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def default_data_dir() -> Path:
    """运行时数据目录(台账等**可变状态**,与日志分居),自动创建。优先级同
    :func:`default_log_dir`:

    1. ``AUTO_SIGNIN_KIT_DATA_DIR`` 环境变量——显式配置,跨平台行为一致;
    2. 项目的 ``data/`` 目录——editable 安装时即仓库根,查看/备份方便;
    3. 平台惯例目录——非项目环境兜底: ``app_data_root()/data``。

    日志(事件流,可清可重建)与数据(台账等,是积累的事实)分目录存放:
    清理日志永远不会误伤台账;备份数据也不用拖上日志。
    """
    explicit = auto_signin_kit_env(_DATA_DIR_ENV)
    if explicit:
        parent = Path(explicit).expanduser()
    else:
        root = project_root()
        # 与 default_log_dir 不同: 数据目录的平台兜底对三大 OS 是同一个
        # app_data_root()/data,不需要分支,可以直接用三元表达式
        parent = root / "data" if root is not None else app_data_root() / "data"
    parent.mkdir(parents=True, exist_ok=True)
    return parent
