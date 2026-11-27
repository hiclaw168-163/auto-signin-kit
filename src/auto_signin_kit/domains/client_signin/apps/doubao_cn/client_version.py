"""豆包客户端版本读取 → AGW ``version_code`` 运行时派生。

服务端按 version_code 校验客户端版本: 写死一个值,客户端升级后就会回
``710010702 系统错误``(公开复盘同因,见 docs/notes/NOTE-04 §4.5)。所以这里
只从本机实际安装的客户端读取版本,再按 ``主*10000 + 次*100 + 修订`` 编码:

- macOS: ``/Applications/Doubao.app``(或 ``~/Applications``)的
  ``Contents/Info.plist`` 里 ``CFBundleShortVersionString``;
- Windows: 卸载注册表 ``DisplayVersion``(由安装器维护,已封装在
  ``core.adapters.client_discovery``);
- 都读不到就**显式报错**,不静默沿用旧值——旧值正是故障本身。

配置 ``client_version`` 可手动覆盖(客户端不在标准路径、或想压测某个版本时)。
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

from auto_signin_kit.core.adapters import client_discovery
from auto_signin_kit.core.adapters.platform_info import IS_MACOS
from auto_signin_kit.core.errors import ConfigError

#: macOS 应用包名(独立安装器落盘为 Doubao.app)。
MAC_BUNDLE = "Doubao.app"
#: 主.次[.修订];服务端只认这个形状,别把 5.74.15 这类运行时版本传进来。
_VERSION_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{1,2}))?$")


def _mac_version() -> str | None:
    """从 .app 的 Info.plist 读版本;包不在或 plist 坏了返回 None。"""
    for root in (Path("/Applications"), Path.home() / "Applications"):
        try:
            with (root / MAC_BUNDLE / "Contents" / "Info.plist").open("rb") as handle:
                raw = plistlib.load(handle).get("CFBundleShortVersionString")
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def installed_version() -> str | None:
    """本机安装的豆包客户端版本;读不到如实返回 None(不猜)。"""
    if IS_MACOS:
        return _mac_version()
    return client_discovery.registry_display_version(r"^Doubao")


def version_to_code(version: str) -> int | None:
    """``2.35.8`` → ``23508``;形状非法返回 None(由上层的显式报错兜底)。"""
    match = _VERSION_RE.match(version.strip())
    if not match:
        return None
    major, minor, patch = match.group(1), match.group(2), match.group(3) or "0"
    return int(major) * 10000 + int(minor) * 100 + int(patch)


def resolve(configured: str | None) -> tuple[str, int]:
    """配置显式值优先,其次本机安装版本;都拿不到就显式报错。"""
    version = (configured or "").strip() or installed_version()
    if not version:
        raise ConfigError(
            "读不到豆包客户端版本(未安装或不在标准路径),无法派生 version_code;"
            "请在 config/domains/doubao_cn.yaml 显式设置 client_version(如 2.35.8)"
        )
    code = version_to_code(version)
    if code is None:
        raise ConfigError("client_version 形状非法(应为主.次[.修订],如 2.35.8): %r" % version)
    return version, code
