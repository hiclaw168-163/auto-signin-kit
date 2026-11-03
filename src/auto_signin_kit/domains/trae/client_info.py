"""Trae 客户端信息采集(只读): 版本/设备标识/数据目录。

数据源是 ``storage.json`` 的明文键(与登录态解封无关,不碰令牌字段);
设备标识直接复用签到设备头的取证结论(:func:`device_meta`)。
"""

from __future__ import annotations

from typing import Any

from auto_signin_kit.domains.trae.auth import data_roaming_roots, device_meta


def client_info() -> dict[str, Any] | None:
    """当前客户端信息;桌面端数据目录不存在时返回 None(未安装,如实呈报)。"""
    roots = [r for r in data_roaming_roots() if r.is_dir()]
    if not roots:
        return None
    meta = device_meta()
    return {
        "version": meta.get("app_version"),
        "device_id": meta.get("aha_device_id") or meta.get("device_id"),
        "data_dir": str(roots[0]),
    }
