"""win_process 进程枚举的回归测试。

2026-11-27 实机故障: ``th32ModuleID`` 误用 ``c_size_t``(8 字节),而 Windows
``PROCESSENTRY32`` 里它是 DWORD(4 字节)。结构体因此整体错位 4 字节,
``szExeFile`` 从 44 偏到 48,读出来的进程名全被截掉前 4 个字符
(``[System Process]`` → ``tem Process]``),任何按名字的匹配都落空——
豆包版本号因此反查不到安装目录,签到直接失败。
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import pytest

from auto_signin_kit.core.adapters import win_process as wp
from auto_signin_kit.core.adapters.platform_info import IS_WINDOWS


def test_module_id_is_dword_not_pointer_sized():
    """th32ModuleID 必须是 4 字节;指针宽度会让其后所有字段错位。"""
    fields = dict(wp._ENTRY._fields_)
    assert ctypes.sizeof(fields["th32ModuleID"]) == 4
    # 紧跟其后的字段必须紧挨着,不能出现为了对齐而多出来的 4 字节空洞
    assert wp._ENTRY.th32Threads.offset == wp._ENTRY.th32ModuleID.offset + 4


@pytest.mark.skipif(not IS_WINDOWS, reason="进程快照枚举仅 Windows 有实现")
def test_running_process_paths_finds_current_process():
    # 当前 python 进程一定在快照里;错位实现下名字被截断,这里必然返回空
    exe = Path(sys.executable)
    found = wp.running_process_paths([exe.name])
    assert found, "进程快照里找不到当前进程,枚举结果不可信"
    assert any(path.name.lower() == exe.name.lower() for path in found)
