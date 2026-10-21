"""Qoder CN 设备风控头: 运行客户端自带的 runtime-info.exe 取设备标识。

标识不编造: 优先用安装目录组件(runtime-info.exe --account-stdin)与
``auth.machine-id``;找不到组件就退回普通请求,不影响主流程。结果带 30 分钟缓存,
避免每轮都起一次子进程。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from auto_signin_kit.core.adapters import native_helper
from auto_signin_kit.core.adapters.client_discovery import registry_install_roots
from auto_signin_kit.core.adapters.platform_info import hostname, machine_arch
from auto_signin_kit.core.adapters.win_process import running_process_paths

UMID_REL_PATH = ("resources", "umid", "runtime-info.exe")

#: "Qoder CN"/"Qoder" 的卸载登记名(仅用于扫 InstallLocation,只读)。
_QODER_NAME_RE = r"Qoder(?:\s+CN)?(?:\s+\d+(?:\.\d+)*)?"

_cache: dict[str, Any] = {"at": 0.0, "root": None, "headers": {}}


def _umid_candidates() -> list[Path]:
    """runtime-info.exe 的候选路径(QODER_UMID_EXE 覆盖;Windows 语义)。"""
    override = os.environ.get("QODER_UMID_EXE")
    if override:
        return [Path(override).expanduser()]
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    prog = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    prog86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    bases = (local, prog, prog86)
    roots: list[Path] = [
        base / "Programs" / name if base is local else base / name
        for base in bases
        for name in ("Qoder CN", "Qoder")
    ]
    roots += registry_install_roots(_QODER_NAME_RE)
    roots += running_process_paths(("qoder cn.exe", "qoder.exe"), parent_dir=True)
    seen: set[str] = set()
    uniq: list[Path] = []
    for root in roots:
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            uniq.append(root)
    return [root.joinpath(*UMID_REL_PATH) for root in uniq]


def _machine_id(auth_dir: str | None) -> str:
    """auth.machine-id 优先用解出登录态的那份数据目录。"""
    candidates: list[Path] = []
    if auth_dir:
        candidates.append(Path(auth_dir))
    candidates += [d for d in find_auth_dirs_safe() if d not in candidates]
    for root in candidates:
        try:
            value = (root / "auth.machine-id").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return ""


def find_auth_dirs_safe() -> list[Path]:
    """复用 auth.py 的目录发现,但不让它抛异常(纯辅助路径来源)。"""
    from auto_signin_kit.domains.qoder.auth import NoAuthError, find_auth_dirs

    try:
        return find_auth_dirs()
    except NoAuthError:
        return []


def _run_umid(exe: Path) -> dict:
    code, stdout, _stderr = native_helper.run_capture(
        str(exe), ("--account-stdin",), timeout=15, cwd=exe.parent
    )
    if code != 0:
        return {}
    for line in reversed(stdout.decode("utf-8", "replace").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        return data if isinstance(data, dict) else {}
    return {}


def risk_headers(auth_dir: str | None = None) -> dict[str, str]:
    """组装设备风控头;结果按 (目录, 30 分钟) 缓存。"""
    from auto_signin_kit.core.adapters.platform_info import IS_WINDOWS

    now = time.time()
    if _cache["root"] == auth_dir and now - float(_cache["at"]) < 1800:
        return dict(_cache["headers"])

    headers: dict[str, str] = {}
    if IS_WINDOWS:
        headers["Cosy-MachineOS"] = "%s_windows" % machine_arch()
        headers["Cosy-MachineHostname"] = hostname()

    exe = next((p for p in _umid_candidates() if p.is_file()), None)
    if exe:
        try:
            manifest = json.loads(
                (exe.parent.parent / "build-manifest.json").read_text(encoding="utf-8")
            )
            version = manifest.get("productVersion")
            if version:
                headers["Cosy-Version"] = str(version)
        except (OSError, ValueError):
            pass
        identity = _run_umid(exe)
        for src, dst in (
            ("machineToken", "Cosy-MachineToken"),
            ("machineCode", "Cosy-MachineCode"),
            ("machineType", "Cosy-MachineType"),
        ):
            value = identity.get(src)
            if value:
                headers[dst] = str(value)

    mid = _machine_id(auth_dir)
    if mid:
        headers["Cosy-MachineId"] = mid

    _cache.update({"at": now, "root": auth_dir, "headers": dict(headers)})
    return headers


def find_device_component() -> str | None:
    """doctor 用: 实际找到的 runtime-info.exe 路径(存在性检查)。"""
    for exe in _umid_candidates():
        if exe.is_file():
            return str(exe)
    return None
