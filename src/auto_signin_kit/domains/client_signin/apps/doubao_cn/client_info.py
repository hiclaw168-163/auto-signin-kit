"""豆包客户端信息采集(只读): 版本/内核版本/数据目录。

- 客户端版本: 卸载注册表 DisplayVersion(DisplayName = "豆包");
- 内核版本: ``Local State`` 的 ``browser.shortcut_migration_version``
  (实测与 CDP /json/version 的 Chrome 版本一致,如 147.0.7727.149);
- 数据目录复用 Cookie 解密同一套发现(:func:`default_user_data_dir`)。
"""

from __future__ import annotations

import json
from typing import Any

from auto_signin_kit.core.adapters.client_discovery import registry_display_version
from auto_signin_kit.domains.client_signin.apps.doubao_cn.cookies import (
    LOCAL_STATE_REL,
    default_user_data_dir,
)

_DISPLAY_NAME_RE = r"豆包|Doubao"


def client_info() -> dict[str, Any] | None:
    """当前客户端信息;数据目录不存在时返回 None(未安装,如实呈报)。"""
    root = default_user_data_dir()
    if not root.is_dir():
        return None
    engine = None
    try:
        state = json.loads((root / LOCAL_STATE_REL).read_text(encoding="utf-8"))
        engine = (state.get("browser") or {}).get("shortcut_migration_version")
    except (OSError, ValueError):
        pass  # Local State 缺失/损坏: 版本仍如实上报
    return {
        "version": registry_display_version(_DISPLAY_NAME_RE),
        "engine": engine,
        "data_dir": str(root),
    }
