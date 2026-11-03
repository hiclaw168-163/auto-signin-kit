"""Qoder 客户端信息采集(只读): 版本/设备标识/数据目录。

- 版本: 卸载注册表 DisplayVersion(安装器维护,比猜文件可靠);
- 设备标识: 数据目录 ``qoder-data.v1.json`` 的 ``deviceId``;
- 数据目录发现复用登录态同一套扫描(:func:`find_auth_dirs`)。
"""

from __future__ import annotations

import json
from typing import Any

from auto_signin_kit.core.adapters.client_discovery import registry_display_version
from auto_signin_kit.domains.qoder.auth import find_auth_dirs

_DATA_FILE = "qoder-data.v1.json"


def client_info() -> dict[str, Any] | None:
    """当前客户端信息;数据目录不存在时返回 None(未安装,如实呈报)。"""
    dirs = [d for d in find_auth_dirs() if d.is_dir()]
    if not dirs:
        return None
    device_id = None
    try:
        data = json.loads((dirs[0] / _DATA_FILE).read_text(encoding="utf-8"))
        device_id = data.get("deviceId")
    except (OSError, ValueError):
        pass  # 数据文件缺失/损坏: 版本与路径仍如实上报,不中断整份档案
    return {
        "version": registry_display_version(r"Qoder( CN)?"),
        "device_id": device_id,
        "data_dir": str(dirs[0]),
    }
