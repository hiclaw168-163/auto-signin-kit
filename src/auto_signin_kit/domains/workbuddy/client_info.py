"""WorkBuddy 客户端信息采集(只读): 版本/安装路径/登录身份。

- 版本: 卸载注册表 DisplayVersion;安装路径复用 runtime 的探测;
- 登录身份: 桌面端凭据文件的 ``account`` 段(nickname/uid/lastLogin);
  手机号是个人敏感信息,**中四位打码后入库**,令牌类字段一个都不读。
"""

from __future__ import annotations

import json
import re
from typing import Any

from auto_signin_kit.core.adapters.client_discovery import registry_display_version
from auto_signin_kit.domains.workbuddy import credentials
from auto_signin_kit.domains.workbuddy.runtime import _runtime_path

_IDENTITY_KEYS = ("nickname", "uid", "uin", "lastLogin")


def _mask_phone(value: Any) -> str | None:
    """手机号打码: 保留前 3 后 4,中间四星;非手机号形态原样返回不做假设。"""
    text = str(value or "")
    return re.sub(r"(^\d{3})\d{4}(\d{4}$)", r"\1****\2", text) or None


def client_info() -> dict[str, Any] | None:
    """当前客户端信息;客户端未安装(_runtime_path 探测失败)返回 None。"""
    try:
        exe = _runtime_path()
    except Exception:
        return None  # AuthError 等探测异常 = 未安装/路径不可用,如实呈报
    if not exe:
        return None
    info: dict[str, Any] = {
        "version": registry_display_version(r"WorkBuddy"),
        "install_path": str(exe),
    }
    auth_file, _looked = credentials.find_auth_file()
    if auth_file:
        try:
            data = json.loads(auth_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        account = data.get("account") or {}
        if isinstance(account, dict):
            # uid 是账户侧唯一标识: 提到档案顶层(落独立列,多账户轮询的关键维度)
            if account.get("uid") and not _is_encrypted(account["uid"]):
                info["uid"] = account["uid"]
            identity = {k: account[k] for k in _IDENTITY_KEYS
                        if account.get(k) is not None
                        and not _is_encrypted(account[k])}
            if account.get("phoneNumber"):
                phone = account["phoneNumber"]
                # 加密信封不落库(密文无档案意义),明文手机号才打码入库
                if not _is_encrypted(phone):
                    masked = _mask_phone(phone)
                    if masked:
                        identity["phone"] = masked
            if identity:
                info["identity"] = identity
    return info


def _is_encrypted(value: Any) -> bool:
    """WorkBuddy 敏感字段的静态加密信封(客户端密文,外部无钥匙)。"""
    return isinstance(value, dict) and "$wbEncrypted" in value
