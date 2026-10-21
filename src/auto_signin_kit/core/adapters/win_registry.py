"""Windows 卸载注册表项扫描: 只读信息,不修改注册表、不执行卸载命令。

读取语义(与客户端安装定位的场景一致,只取信息、不碰注册表写入):
遍历 HKCU/HKLM 两个 hive 的 64/32 位视图,对每个卸载条目只读取字符串值;
删除条目与权限不足的子键按"继续下一个"处理,视为正常。
"""

from __future__ import annotations

import winreg
from collections.abc import Callable, Iterator
from typing import Any

_UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"


def _read_string(registry: Any, key: Any, field: str) -> str | None:
    """读字符串型值;REG_EXPAND_SZ 用系统展开环境变量后返回。"""
    try:
        value, kind = registry.QueryValueEx(key, field)
        if not isinstance(value, str) or kind not in (registry.REG_SZ, registry.REG_EXPAND_SZ):
            return None
        if kind == registry.REG_EXPAND_SZ:
            value = registry.ExpandEnvironmentStrings(value)
        return value
    except OSError:
        return None


def uninstall_entries() -> Iterator[Callable[[str], str | None]]:
    """逐个产出卸载条目的"字段读取函数";调用方按需取 DisplayName 等字段。

    枚举顺序: HKCU 先于 HKLM,64 位视图先于 32 位视图——与原实现一致,
    后写者可据此决定优先级(先发现的路径更可信)。
    """
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                root = winreg.OpenKey(hive, _UNINSTALL, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            try:
                count = winreg.QueryInfoKey(root)[0]
                for index in range(count):
                    try:
                        key = winreg.OpenKey(
                            root, winreg.EnumKey(root, index), 0, winreg.KEY_READ | view
                        )
                    except OSError:
                        # 已删除条目与权限不足的子键是正常情况。
                        continue
                    try:
                        yield (lambda k: lambda field: _read_string(winreg, k, field))(key)
                    finally:
                        winreg.CloseKey(key)
            finally:
                winreg.CloseKey(root)
