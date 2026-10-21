"""OS 探测与主机信息: 平台分支只允许出现在本模块(core/adapters/ 层)。"""

from __future__ import annotations

import platform as _platform
import socket
import sys

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def machine_arch() -> str:
    """把本机 CPU 架构归一成 x86_64/aarch64 两类。"""
    machine = _platform.machine().lower()
    mapping = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
    }
    return mapping.get(machine, "x86_64")


def hostname() -> str:
    """本机主机名。"""
    return socket.gethostname()


def os_tag() -> str:
    """当前 OS 标识(用于诊断输出,不做业务分支)。"""
    return sys.platform
