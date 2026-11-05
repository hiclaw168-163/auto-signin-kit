"""Qoder 客户端信息采集(只读): 版本/设备标识/数据目录/版本区。

- 版本: 卸载注册表 DisplayVersion(安装器维护,比猜文件可靠);
- 设备标识: 数据目录 ``qoder-data.v1.json`` 的 ``deviceId``;
- 数据目录发现复用登录态同一套扫描(:func:`find_auth_dirs`);
- region(版本区)按数据目录名判: bundle id 含 ``qodercn`` = 国内版,
  ``com.qoder.app*`` = 国际版(NOTE-05 实测两版命名)。不为此解锁登录态
  (Keychain 探测在 UI 同步路径里会打扰用户),目录名是稳定充分的信号。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from auto_signin_kit.core.adapters.client_discovery import registry_display_version
from auto_signin_kit.domains.client_signin.apps.qoder.auth import find_auth_dirs

_DATA_FILE = "qoder-data.v1.json"


def _region(data_dir: Path) -> str:
    """版本区: 数据目录名含 ``qodercn`` 为国内版,否则国际版。"""
    return "cn" if "qodercn" in data_dir.name.lower() else "intl"


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
        "region": _region(dirs[0]),
    }
