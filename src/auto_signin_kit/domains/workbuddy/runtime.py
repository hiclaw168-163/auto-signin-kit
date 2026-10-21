"""WorkBuddy 客户端可执行文件定位: 环境变量 → 默认目录 → 卸载注册表项。

只在默认目录失败时才读注册表;注册表仅读取信息,不修改、不执行卸载命令。
"""

from __future__ import annotations

import os

from auto_signin_kit.core.adapters import client_discovery
from auto_signin_kit.core.httpclient import Budget
from auto_signin_kit.domains.workbuddy.session import AuthError

#: DisplayName 精确匹配 WorkBuddy(可带版本号);大小写不敏感。
_DISPLAY_NAME_RE = r"WorkBuddy(?:\s+\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.-]+)?)?"


def find_workbuddy_runtime(budget: Budget) -> str:
    """定位客户端运行时可执行文件;找不到/路径无效时抛 AUTH 域错误。

    助手子进程与本次运行预算共用同一时间预算,超时用 ``min(助手超时, 预算)``。
    """
    del budget  # 定位本身零网络请求;签名保留以便未来探测型定位共享预算
    override = os.environ.get("WORKBUDDY_EXE")
    if override:
        return str(client_discovery.override_executable(
            override, AuthError("INVALID_RUNTIME_PATH")
        ))
    found = client_discovery.default_roots("WorkBuddy", "WorkBuddy.exe", "WorkBuddy.app")
    if found:
        return str(found[0])
    registered = client_discovery.registry_install_paths(_DISPLAY_NAME_RE, "WorkBuddy.exe")
    if registered:
        return str(registered[0])
    raise AuthError("RUNTIME_NOT_FOUND")
