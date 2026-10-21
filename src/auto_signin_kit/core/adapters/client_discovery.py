"""通用桌面客户端(Electron 系)安装位置发现: 默认目录 / 注册表 / macOS bundle。

OS 分支只出现在这里;调用方(业务域)只描述"这个应用的产物长什么样",
不关心去哪个操作系统路径找。
"""

from __future__ import annotations

import ntpath
import os
import re
from pathlib import Path

from auto_signin_kit.core.adapters.platform_info import IS_MACOS, IS_WINDOWS
from auto_signin_kit.core.errors import AdapterError


class ClientDiscoveryError(AdapterError):
    """定位客户端失败;调用方按需转译成域内错误。"""


def mac_bundle_executable(bundle: Path) -> Path | None:
    """从 .app 包里找实际可执行文件(CFBundleExecutable);格式可疑返回 None。"""
    import plistlib

    try:
        with (bundle / "Contents" / "Info.plist").open("rb") as handle:
            name = plistlib.load(handle).get("CFBundleExecutable")
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\\" in name:
        return None
    return bundle / "Contents" / "MacOS" / name


def default_roots(app_name: str, exe_name: str, mac_bundle: str) -> list[Path]:
    """按 OS 枚举该应用的常见安装位置,返回实际存在的可执行文件路径。"""
    home = Path.home()
    if IS_WINDOWS:
        localappdata = os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local"
        candidates = [Path(localappdata) / "Programs" / app_name / exe_name]
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            value = os.environ.get(env_name)
            if value:
                candidates.append(Path(value) / app_name / exe_name)
        return [path for path in candidates if path.is_file()]
    if IS_MACOS:
        out: list[Path] = []
        for root in (Path("/Applications"), home / "Applications"):
            exe = mac_bundle_executable(root / mac_bundle)
            if exe and exe.is_file() and os.access(exe, os.X_OK):
                out.append(exe)
        return out
    return []


def _runtime_path_from_value(value: object, exe_name: str, *, icon: bool) -> str | None:
    """把注册表值解析成 exe 路径;绝不解释卸载命令(ntpath 语义)。"""
    text = value if isinstance(value, str) else None
    if not text or re.search(r"[\x00-\x1f\x7f]", text):
        return None
    text = text.strip()
    if text.startswith('"'):
        suffix = r"(?:\s*,\s*-?\d+)?" if icon else ""
        match = re.fullmatch(r'"([^"]+)"' + suffix, text)
        if not match:
            return None
        text = match[1]
    elif '"' in text:
        return None
    elif icon:
        # 只移除尾部的资源索引;逗号可能属于文件夹名本身。
        text = re.sub(r",\s*-?\d+$", "", text).rstrip()
    if not ntpath.splitdrive(text)[0] or not ntpath.isabs(text):
        return None
    text = ntpath.join(text, exe_name) if not icon else text
    text = ntpath.normpath(text)
    if ntpath.basename(text).casefold() != exe_name.casefold():
        return None
    return text


def registry_install_paths(
    display_name_re: str, exe_name: str, *, dedupe: bool = True
) -> list[Path]:
    """扫描卸载注册表项,按 DisplayName 过滤并解析出已存在的 exe 路径。

    过期 InstallLocation 不应挡住有效的 DisplayIcon,所以两个字段都试;
    normcase 去重,行为对齐原实现。仅在 Windows 上有结果,其它平台返回 []。
    """
    if not IS_WINDOWS or not exe_name:
        return []
    from auto_signin_kit.core.adapters.win_registry import uninstall_entries

    seen: set[str] = set()
    out: list[Path] = []
    for get in uninstall_entries():
        display_name = get("DisplayName")
        if not display_name or not re.fullmatch(
            display_name_re, display_name.strip(), re.IGNORECASE
        ):
            continue
        for field, icon in (("InstallLocation", False), ("DisplayIcon", True)):
            text = _runtime_path_from_value(get(field), exe_name, icon=icon)
            if not text:
                continue
            key = ntpath.normcase(text)
            if dedupe and key in seen:
                continue
            seen.add(key)
            if Path(text).is_file():
                out.append(Path(text))
    return out


def registry_install_roots(display_name_re: str) -> list[Path]:
    """按 DisplayName 过滤卸载条目,仅用 InstallLocation 解析出已存在的安装根目录。

    与 ``registry_install_paths`` 不同,这里不做 exe 拼接——调用方自己往根目录里
    追加相对路径(例如 Qoder 的 resources/umid/runtime-info.exe)。
    """
    if not IS_WINDOWS:
        return []
    from auto_signin_kit.core.adapters.win_registry import uninstall_entries

    seen: set[str] = set()
    out: list[Path] = []
    for get in uninstall_entries():
        display_name = get("DisplayName")
        if not display_name or not re.fullmatch(
            display_name_re, display_name.strip(), re.IGNORECASE
        ):
            continue
        text = get("InstallLocation")
        if not isinstance(text, str) or re.search(r"[\x00-\x1f\x7f]", text):
            continue
        text = text.strip()
        if not ntpath.splitdrive(text)[0] or not ntpath.isabs(text) or '"' in text:
            continue
        root = ntpath.normpath(text)
        key = ntpath.normcase(root)
        if key in seen:
            continue
        seen.add(key)
        if Path(root).is_dir():
            out.append(Path(root))
    return out


def override_executable(env_value: str, error: ClientDiscoveryError) -> Path:
    """环境变量显式指定的可执行文件;路径错误时抛出给定异常,不静默回退。"""
    path = Path(os.path.expandvars(env_value.strip())).expanduser().absolute()
    if not path.is_file():
        raise error
    if not IS_WINDOWS and not os.access(path, os.X_OK):
        raise error
    return path
