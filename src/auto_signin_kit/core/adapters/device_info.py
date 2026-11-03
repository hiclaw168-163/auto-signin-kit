"""本机设备标识(只读): hostname / OS 版本 / MachineGuid(脱敏)。

MachineGuid 只留首 8 末 4 位——它是整机唯一指纹,全量落库属于过度收集;
账户档案要关联的是"这台机器",不是要能复现指纹。
"""

from __future__ import annotations

import platform
import socket

from auto_signin_kit.core.adapters.platform_info import IS_WINDOWS


def machine_info() -> dict[str, str]:
    """本机设备信息;MachineGuid 读不到(非 Windows/权限)时缺省该键。"""
    info = {"hostname": socket.gethostname(), "os": platform.platform()}
    guid = _machine_guid()
    if guid:
        info["machine_guid"] = "%s...%s" % (guid[:8], guid[-4:])
    return info


def _machine_guid() -> str | None:
    if not IS_WINDOWS:
        return None
    import winreg

    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
        return winreg.QueryValueEx(key, "MachineGuid")[0]
    except OSError:
        return None
