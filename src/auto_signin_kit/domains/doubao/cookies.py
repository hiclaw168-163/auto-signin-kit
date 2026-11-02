"""从豆包客户端(Chromium 内核)本地数据目录解出会话 Cookie。

取证结论(docs/notes/NOTE-04,2026-11-02 实测):

- 登录态在 Chromium Cookie 库里,可能分布在多个 profile:
  ``Default/Network/Cookies``(登录页/主窗口)与 ``Profile N/Network/Cookies``
  (活动面板 webview,**持有最新会话**)。取值按库文件修改时间新者优先;
- cookie 值为标准 v10 加密(前缀 ``v10`` + 12 字节 nonce + AES-256-GCM 密文),
  密钥在 ``<User Data>/Local State`` 的 ``os_crypt.encrypted_key``
  (DPAPI 包裹,当前用户可解);
- 解密后的明文有一个 **32 字节应用层前缀**(客户端自加),剥离后才是真实值;
- ``Default`` 库运行时可用共享读绕锁;``Profile N`` 库被 webview 独占打开
  (WinError 32),客户端运行期间读不了——两个兜底:
  a) 客户端正常退出后 Cookie 会落盘,关闭客户端再跑一次即可;
  b) 客户端带 ``--remote-debugging-port`` 启动时,直接走 CDP 拿实时 Cookie
  (见 ``cdp.cookies_via_cdp``),实时值优先级最高;
- 仅 Windows 可解(DPAPI);其他平台如实报 UNSUPPORTED,不做静默兜底。
"""

from __future__ import annotations

import base64
import contextlib
import ctypes
import ctypes.wintypes as wt
import json
import os
import sqlite3
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from auto_signin_kit.core.adapters.win_dpapi import DecryptError, decrypt_gcm, unprotect
from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.domains.doubao.cdp import cookies_via_cdp

#: 默认 Chromium 数据目录(%LOCALAPPDATA%/Doubao/User Data)。
DEFAULT_USER_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Doubao" / "User Data"
COOKIES_REL = Path("Default") / "Network" / "Cookies"
LOCAL_STATE_REL = Path("Local State")
#: 鉴权必需的 cookie 名;缺任何一个都视为登录态不可用。
REQUIRED_COOKIES = ("sessionid", "sid_tt", "uid_tt")
#: 应用层值前缀长度(字节);见模块 docstring。
APP_LAYER_VALUE_PREFIX = 32
#: CDP 调试端口兜底(DOUBAO_CDP_PORT 优先);客户端须带 --remote-debugging-port 启动。
DEFAULT_CDP_PORT = 9222


class DoubaoCookieError(AutoSignInKitError):
    """Cookie 解密链路失败(客户端未装/文件不可读/密钥不可解)。"""

    exit_code = 2


def default_user_data_dir() -> Path:
    """豆包客户端数据目录;环境变量 DOUBAO_USER_DATA_DIR 优先。"""
    env = os.environ.get("DOUBAO_USER_DATA_DIR")
    return Path(env) if env else DEFAULT_USER_DATA_DIR


def _copy_locked_file(src: Path, dst: Path) -> None:
    """共享读模式拷贝被独占锁打开的文件(客户端运行时 Cookies 库被锁)。

    用 CreateFileW 带 FILE_SHARE_READ|WRITE|DELETE 打开;打不开(锁更严/权限
    不足)抛 ``DoubaoCookieError``,由上层如实报告——不静默跳过。
    """
    generic_read = 0x80000000
    share_all = 0x1 | 0x2 | 0x4
    open_existing = 3
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateFileW(str(src), generic_read, share_all, None,
                                  open_existing, 0, None)
    if handle in (-1, 0xFFFFFFFFFFFFFFFF):
        raise DoubaoCookieError(
            "无法读取 Cookie 库(可能被客户端独占锁定): %s (WinError %s)"
            % (src, ctypes.get_last_error()))
    try:
        size = kernel32.SetFilePointer(handle, 0, None, 2)  # FILE_END
        kernel32.SetFilePointer(handle, 0, None, 0)  # FILE_BEGIN
        buffer = ctypes.create_string_buffer(size)
        read = wt.DWORD()
        if not kernel32.ReadFile(handle, buffer, size, ctypes.byref(read), None):
            raise DoubaoCookieError("Cookie 库读取失败: %s" % src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(buffer.raw[: read.value])
    finally:
        kernel32.CloseHandle(handle)


def _aes_key(local_state_path: Path) -> bytes:
    """从 Local State 解出 os_crypt 的 AES-256 密钥(DPAPI,仅当前用户可解)。"""
    try:
        data = json.loads(local_state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DoubaoCookieError("Local State 不可读: %s (%s)" % (local_state_path, exc)) from exc
    wrapped = (data.get("os_crypt") or {}).get("encrypted_key")
    if not wrapped:
        raise DoubaoCookieError("Local State 缺少 os_crypt.encrypted_key")
    raw = base64.b64decode(wrapped)
    if raw[:5] != b"DPAPI":
        raise DoubaoCookieError("encrypted_key 前缀异常(非 DPAPI)")
    try:
        return unprotect(raw[5:])
    except DecryptError as exc:
        raise DoubaoCookieError(
            "DPAPI 解密密钥失败(须以登录豆包的同一 Windows 用户运行): %s" % exc) from exc


def _decrypt_cookie_value(aes_key: bytes, encrypted: bytes) -> str | None:
    """解单个 cookie 值;非 v10/解密失败返回 None(跳过,不中断整批)。"""
    if not encrypted.startswith(b"v10"):
        return None
    nonce, blob = encrypted[3:15], encrypted[15:]
    try:
        plain = decrypt_gcm(aes_key, nonce, blob)
    except DecryptError:
        return None
    if len(plain) <= APP_LAYER_VALUE_PREFIX:
        return None
    value = plain[APP_LAYER_VALUE_PREFIX:].decode("utf-8", "replace")
    return value if value and value.isprintable() else None


def _candidate_dbs(user_data_dir: Path) -> list[Path]:
    """全部候选 Cookie 库,按修改时间新者优先(新会话赢旧会话)。"""
    candidates = [user_data_dir / COOKIES_REL]
    candidates += sorted(user_data_dir.glob("Profile */Network/Cookies"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    return [p for p in candidates if p.exists()]


def _read_db(db_path: Path, aes_key: bytes) -> dict[str, str]:
    """读单库并解密;库被锁/不存在抛 ``DoubaoCookieError``。"""
    dst = Path(tempfile.gettempdir()) / "auto-signin-kit-doubao-cookies.db"
    try:
        _copy_locked_file(db_path, dst)
    except DoubaoCookieError:
        raise
    except OSError as exc:
        raise DoubaoCookieError("Cookie 库拷贝失败: %s" % exc) from exc
    try:
        conn = sqlite3.connect(str(dst))
        try:
            rows = conn.execute(
                "select host_key, name, encrypted_value from cookies "
                "where host_key like '%doubao.com'").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise DoubaoCookieError("Cookie 库打开失败: %s" % exc) from exc
    finally:
        with contextlib.suppress(OSError):
            dst.unlink()
    # 先收全量解密结果,再按"域级('.'开头)cookie 优先"选同名者
    decrypted: list[tuple[str, str, str]] = []
    for host, name, encrypted in rows:
        value = _decrypt_cookie_value(aes_key, bytes(encrypted))
        if value:
            decrypted.append((host, name, value))
    pairs: dict[str, str] = {}
    for prefer_dot in (True, False):
        for host, name, value in decrypted:
            if name in pairs or host.startswith(".") != prefer_dot:
                continue
            pairs[name] = value
    return pairs


def load_cookie_header(user_data_dir: Path | None = None) -> str:
    """解出豆包会话 Cookie 并拼成请求头;失败抛 ``DoubaoCookieError``。

    合并顺序: 文件库按修改时间新者优先;若客户端带调试端口运行,CDP 实时值
    最后覆盖(它才是浏览器当前真正在用的会话——文件库可能是未刷新的旧会话,
    实测表现为服务端回"登录已过期")。值本身不进任何日志/输出。
    """
    root = user_data_dir or default_user_data_dir()
    candidates = _candidate_dbs(root)
    if not candidates:
        raise DoubaoCookieError(
            "Cookie 库不存在(豆包客户端未安装或未登录过?): %s" % root)
    key = _aes_key(root / LOCAL_STATE_REL)
    merged: dict[str, str] = {}
    errors: list[str] = []
    for db_path in candidates:
        try:
            pairs = _read_db(db_path, key)
        except DoubaoCookieError as exc:
            errors.append("%s: %s" % (db_path.parent.parent.name, exc))
            continue
        for name, value in pairs.items():
            merged.setdefault(name, value)
    # 总是尝试 CDP: 文件库可能持有"格式有效但已被服务端判过期"的旧会话
    # (实测: 更新客户端重登后 Default 仍是旧 sessionid,服务端回"登录已过期"),
    # CDP 可达时实时值一定是最权威的;客户端未带调试端口时 1 秒即返回 None。
    live = cookies_via_cdp()
    if live:
        merged.update(live)  # CDP 实时值优先级最高
    missing = [n for n in REQUIRED_COOKIES if n not in merged]
    if missing:
        raise DoubaoCookieError(
            "登录态不完整: 缺 %s。常见原因: %s。可关闭豆包客户端让 Cookie 落盘后"
            "重试(退出时自动落盘),或以 --remote-debugging-port 启动客户端走 CDP 兜底"
            % (",".join(missing), "; ".join(errors) if errors else "解密失败"))
    return "; ".join("%s=%s" % (n, v) for n, v in sorted(merged.items()))


def cdp_available() -> bool:
    """CDP 调试端口是否可达(客户端带 --remote-debugging-port 运行)。"""
    try:
        port = int(os.environ.get("DOUBAO_CDP_PORT", DEFAULT_CDP_PORT))
        with urllib.request.urlopen(
                "http://127.0.0.1:%s/json/version" % port, timeout=1) as resp:
            return resp.status == 200
    except (OSError, ValueError):
        return False


def doctor_report(user_data_dir: Path | None = None) -> dict[str, Any]:
    """离线体检: 各环节逐一检查并给出结论,供 doctor 命令输出。"""

    root = user_data_dir or default_user_data_dir()
    checks: dict[str, Any] = {
        "user_data_dir": str(root),
        "local_state_exists": (root / LOCAL_STATE_REL).exists(),
        "cookie_dbs": [str(p.relative_to(root)) for p in _candidate_dbs(root)],
        "cdp_available": cdp_available(),
    }
    if checks["cookie_dbs"] and checks["local_state_exists"]:
        try:
            load_cookie_header(root)
            checks["session_ok"] = True
        except DoubaoCookieError as exc:
            checks["session_ok"] = False
            checks["session_error"] = str(exc)
    else:
        checks["session_ok"] = False
        checks["session_error"] = "Cookie 库或 Local State 缺失"
    return checks
