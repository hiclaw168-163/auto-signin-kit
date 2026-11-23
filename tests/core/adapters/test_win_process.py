"""win_process 适配层回归测试: 进程快照结构布局与名称匹配。

2026-11-22 实测踩坑: ``_ENTRY.th32ModuleID`` 误声明为 ``c_size_t``(8 字节),
真实 Win32 ``PROCESSENTRY32.th32ModuleID`` 是 ``DWORD``(4 字节),结构体整体
多 8 字节导致 ``szExeFile`` 读偏——枚举能跑通(拿到一堆进程),但名字全部错位,
``running_process_paths`` 永远返回空,静默失效。qoder 国际版因此取不到设备
组件、拿不到每日签到活动。
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import pytest

from auto_signin_kit.core.adapters import win_process
from auto_signin_kit.core.adapters.platform_info import IS_WINDOWS

pytestmark = pytest.mark.skipif(not IS_WINDOWS, reason="仅 Windows 有进程快照 API")


def test_process_entry_layout_matches_win32() -> None:
    """64 位下 PROCESSENTRY32 实测 304 字节;SZExe 字段读偏即此断言失败。"""
    if sys.maxsize <= 2**32:
        pytest.skip("32 位指针下结构体尺寸不同")
    assert ctypes.sizeof(win_process._ENTRY) == 304


def test_running_process_paths_matches_own_process(monkeypatch) -> None:
    """快照必须能按 exe 名匹配到当前进程(锁住字段偏移,防再次静默返回空)。"""
    exe_name = Path(sys.executable).name
    monkeypatch.setattr(
        win_process, "_exe_path_of", lambda _kernel32, _pid: Path(r"C:\fake") / exe_name
    )
    found = win_process.running_process_paths((exe_name,))
    assert found, "快照名称匹配失效: szExeFile 偏移很可能又错了"
    assert all(p.name.lower() == exe_name.lower() for p in found)
