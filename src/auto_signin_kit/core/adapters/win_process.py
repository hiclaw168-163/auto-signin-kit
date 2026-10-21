"""Windows 运行中进程的 exe 路径发现(进程快照枚举,只读)。"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from auto_signin_kit.core.adapters.platform_info import IS_WINDOWS
from auto_signin_kit.core.logging import log_debug

_SNAPSHOT_ALL = 0x2
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def running_process_paths(exe_names: Iterable[str], *, parent_dir: bool = False) -> list[Path]:
    """枚举运行中的进程,返回名字匹配(不区分大小写)的那些的 exe 路径。

    ``parent_dir=True`` 时返回 exe 所在目录(常见于"从运行中的客户端反查安装根")。
    快照/打开句柄失败按正常情况返回空列表——这只用于"锦上添花"的发现,不是必需路径。
    """
    wanted = {name.lower() for name in exe_names if name}
    if not IS_WINDOWS or not wanted:
        return []
    kernel32 = ctypes.windll.kernel32
    roots: list[Path] = []
    try:
        snapshot = kernel32.CreateToolhelp32Snapshot(_SNAPSHOT_ALL, 0)
        if snapshot in (0, -1):
            return roots
        try:
            entry = _ENTRY()
            entry.dwSize = ctypes.sizeof(_ENTRY)
            ok = kernel32.Process32First(snapshot, ctypes.byref(entry))
            while ok:
                name = entry.szExeFile.decode("utf-8", "replace").lower()
                if name in wanted:
                    path = _exe_path_of(kernel32, entry.th32ProcessID)
                    if path:
                        roots.append(path.parent if parent_dir else path)
                ok = kernel32.Process32Next(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
    except Exception as exc:  # noqa: BLE001 — 进程发现是锦上添花,失败降级为已有结果并留痕
        log_debug("进程快照枚举失败: %s: %s" % (type(exc).__name__, exc))
        return roots
    return roots


class _ENTRY(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_size_t), ("th32Threads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260),
    ]


def _exe_path_of(kernel32: Any, pid: int) -> Path | None:
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value[:size.value])
    except Exception as exc:  # noqa: BLE001 — 单个进程的 exe 路径拿不到就跳过,留痕即可
        log_debug("进程 exe 路径查询失败: %s: %s" % (type(exc).__name__, exc))
        return None
    finally:
        kernel32.CloseHandle(handle)
    return None
