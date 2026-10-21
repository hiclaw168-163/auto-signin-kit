"""环境变量读取: 统一 ``AUTO_SIGNIN_KIT_*`` 前缀,避免各处散着拼字符串。"""

from __future__ import annotations

import os


def auto_signin_kit_env(name: str) -> str | None:
    """读取 ``AUTO_SIGNIN_KIT_<name>``;未设置或空串返回 None。"""
    return os.environ.get(f"AUTO_SIGNIN_KIT_{name}") or None
