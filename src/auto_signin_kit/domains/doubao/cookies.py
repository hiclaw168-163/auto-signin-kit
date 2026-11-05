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
- macOS 目录布局与 Windows 不同: 数据目录就是
  ``~/Library/Application Support/Doubao`` 本身(无 "User Data" 层),
  Cookie 库在 ``Default/Cookies`` / ``Profile N/Cookies``(无 ``Network/`` 子层);
- macOS 的 Keychain 密钥(``Doubao Browser Safe Storage``)可读,但 cookie
  值的加密是魔改 os_crypt(密文带应用层头,黑盒穷尽约 300 组合无一命中)——
  文件解密如实报 UNSUPPORTED,mac 链路只走 CDP;
- 仅 Windows 可文件解密(DPAPI);其他平台不做静默兜底。
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
from pathlib import Path
from typing import Any

from auto_signin_kit.core.adapters.platform_info import IS_WINDOWS
from auto_signin_kit.core.adapters.win_dpapi import DecryptError, decrypt_gcm, unprotect
from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.domains.doubao import cdp
from auto_signin_kit.domains.doubao.cdp import cookies_via_cdp

if IS_WINDOWS:  # 平台布局分支,import 时定死(测试可 monkeypatch 常量)
    COOKIES_REL = Path("Default") / "Network" / "Cookies"
    _PROFILES_GLOB = "Profile */Network/Cookies"
else:
    # macOS: 目录就是 Doubao 本身(无 "User Data" 层),Cookie 库无 Network/ 子层
    COOKIES_REL = Path("Default") / "Cookies"
    _PROFILES_GLOB = "Profile */Cookies"
LOCAL_STATE_REL = Path("Local State")
#: 鉴权必需的 cookie 名;缺任何一个都视为登录态不可用。
REQUIRED_COOKIES = ("sessionid", "sid_tt", "uid_tt")
#: 应用层值前缀长度(字节);见模块 docstring。
APP_LAYER_VALUE_PREFIX = 32
#: CDP 调试端口兜底(DOUBAO_CDP_PORT 优先);客户端须带 --remote-debugging-port 启动。
DEFAULT_CDP_PORT = 9222
#: 非 Windows 平台的如实结论 + CDP 指引(不做假支持)。
MAC_DECRYPT_UNSUPPORTED = (
    "macOS 豆包客户端的 Cookie 加密为魔改 Chromium os_crypt(密文带应用层头,"
    "黑盒穷尽失败),暂不支持文件库解密;可走 CDP 兜底: 完全退出豆包后执行 "
    'DOUBAO_CDP_PORT=9223 "/Applications/Doubao.app/Contents/Helpers/'
    'Doubao Browser.app/Contents/MacOS/Doubao Browser" --remote-debugging-port=9223'
)


class DoubaoCookieError(AutoSignInKitError):
    """Cookie 解密链路失败(客户端未装/文件不可读/密钥不可解)。"""

    exit_code = 2


def default_user_data_dir() -> Path:
    """豆包客户端数据目录;环境变量 DOUBAO_USER_DATA_DIR 优先。

    Windows: %LOCALAPPDATA%/Doubao/User Data;macOS: 数据目录就是
    ``~/Library/Application Support/Doubao`` 本身(无 "User Data" 层)。
    """
    env = os.environ.get("DOUBAO_USER_DATA_DIR")
    if env:
        return Path(env)
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Doubao" / "User Data"
    return Path.home() / "Library" / "Application Support" / "Doubao"


def _copy_locked_file(src: Path, dst: Path) -> None:
    """拷贝可能被客户端独占锁打开的 Cookies 库(按平台分派)。

    Windows 用 CreateFileW 带 FILE_SHARE_READ|WRITE|DELETE 打开;打不开
    (锁更严/权限不足)抛 ``DoubaoCookieError``,由上层如实报告——不静默跳过。
    POSIX 没有强制文件锁,普通读即可;真机非 Windows 会在 DPAPI 解密处显式报错。
    """
    if not IS_WINDOWS:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        return
    generic_read = 0x80000000
    share_all = 0x1 | 0x2 | 0x4
    open_existing = 3
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateFileW(str(src), generic_read, share_all, None,
                                  open_existing, 0, None)
    if handle in (-1, 0xFFFFFFFFFFFFFFFF):
        # WinError 32(共享冲突) = 客户端正在运行并独占持有该库,这是确定性事实,
        # 措辞不写"可能": 登录态此刻只在客户端内存里,文件通道就是读不到。
        raise DoubaoCookieError(
            "Cookie 库被运行中的客户端独占锁定(WinError %s): %s"
            % (ctypes.get_last_error(), src))
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


def _db_locked(db_path: Path) -> bool:
    """库文件是否被独占锁定(WinError 32 = 客户端正在运行的可靠信号)。

    doctor 用它把"客户端在跑"作为独立检查项呈报,而不是藏在错误文案里。
    """
    if not IS_WINDOWS:
        return False
    generic_read = 0x80000000
    share_none = 0
    open_existing = 3
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateFileW(str(db_path), generic_read, share_none, None,
                                  open_existing, 0, None)
    if handle in (-1, 0xFFFFFFFFFFFFFFFF):
        return ctypes.get_last_error() == 32  # ERROR_SHARING_VIOLATION
    kernel32.CloseHandle(handle)
    return False


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
    candidates += sorted(user_data_dir.glob(_PROFILES_GLOB),
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


def load_cookie_header(user_data_dir: Path | None = None,
                       cdp_port: int | None = None) -> str:
    """解出豆包会话 Cookie 并拼成请求头;失败抛 ``DoubaoCookieError``。

    合并顺序: 文件库按修改时间新者优先;若客户端带调试端口运行,CDP 实时值
    最后覆盖(它才是浏览器当前真正在用的会话——文件库可能是未刷新的旧会话,
    实测表现为服务端回"登录已过期")。值本身不进任何日志/输出。

    ``user_data_dir``/``cdp_port``(SPEC-12 P2): 具名账户的显式数据目录与
    调试端口(多开豆包各一份 User Data、各一个 --remote-debugging-port);
    缺省(None)沿用环境变量与默认探测链路,行为不变。

    **不做兜底式包装**: 两个读取通道(文件解密/CDP)各自的失败原因按原样抛出,
    缺 cookie 时按"CDP 可达但没取到 / 双通道均失败 / 文件读到了但登录态不完整"
    三种确定情形分别报错,绝不合并成一句模糊的"登录态不完整"。
    """
    try:
        port = cdp.resolve_port(cdp_port)
    except ValueError as exc:
        raise DoubaoCookieError("CDP 端口配置非法(DOUBAO_CDP_PORT/cdp_port): %s" % exc) from exc
    root = user_data_dir or default_user_data_dir()
    candidates = _candidate_dbs(root)
    if not candidates:
        raise DoubaoCookieError(
            "Cookie 库不存在(豆包客户端未安装或未登录过?): %s" % root)
    merged: dict[str, str] = {}
    errors: list[str] = []
    if IS_WINDOWS:
        # 文件解密只有 Windows 支持(DPAPI);其他平台不假装能解,只走 CDP。
        key = _aes_key(root / LOCAL_STATE_REL)
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
    live = cookies_via_cdp(port=port)
    if live:
        merged.update(live)  # CDP 实时值优先级最高
    missing = [n for n in REQUIRED_COOKIES if n not in merged]
    if not missing:
        return "; ".join("%s=%s" % (n, v) for n, v in sorted(merged.items()))
    missing_str = ",".join(missing)
    if cdp.probe(port):
        # 端口通但拿不到会话: 多半是没有打开的 doubao 页面(target 过滤后为空)
        raise DoubaoCookieError(
            "CDP 已连通(端口 %s)但未取到完整登录态(缺 %s): 调试端口上没有打开的"
            " doubao 页面(请在豆包客户端内保持在已登录界面),或 CDP 会话查询失败"
            % (port, missing_str))
    if not IS_WINDOWS:
        raise DoubaoCookieError(
            "%s (CDP 端口 %s 不可达,缺 %s)"
            % (MAC_DECRYPT_UNSUPPORTED, port, missing_str))
    if errors:
        # 双通道均失败: 把两边的真实错误原样抛出,不做兜底包装。
        raise DoubaoCookieError(
            "登录态两个读取通道均失败——①文件库: %s;②CDP: 调试端口 %s 不可达"
            "(豆包未带 --remote-debugging-port 启动)。客户端运行期间登录态只在内存:"
            "用桌面快捷方式 Doubao-signin.lnk 重启豆包(自带调试端口),或托盘右键"
            "完全退出豆包让 Cookie 落盘后重试" % ("; ".join(errors), port))
    # 文件解密成功但缺字段: 磁盘上的登录态确实不完整(过期/未登录)。
    raise DoubaoCookieError(
        "登录态不完整: 缺 %s(文件库解密成功但没有这些字段,磁盘登录态已过期:"
        "重新登录豆包客户端后重试)" % missing_str)


def cdp_available(cdp_port: int | None = None) -> bool:
    """调试端口可达 **且** 确为豆包客户端(校验 /json/version 的 Browser 字段)。

    只探端口会误报: 9222 也可能被其他 Chromium 应用占用(实测 Qoder IDE)。
    """
    try:
        return cdp.probe(cdp.resolve_port(cdp_port))
    except ValueError:
        return False


def doctor_report(user_data_dir: Path | None = None,
                  cdp_port: int | None = None) -> dict[str, Any]:
    """离线体检: 各环节逐一检查并给出结论,供 doctor 命令输出。

    ``cdp_port``(SPEC-12): 具名账户的显式调试端口(多开豆包各一个端口);
    缺省沿用域配置与环境变量链路,行为不变。
    """

    try:
        port = cdp.resolve_port(cdp_port)
    except ValueError:
        port = None  # 非法端口如实落 checks,不炸整个体检
    root = user_data_dir or default_user_data_dir()
    dbs = _candidate_dbs(root)
    checks: dict[str, Any] = {
        "user_data_dir": str(root),
        "file_decrypt_supported": IS_WINDOWS,
        "local_state_exists": (root / LOCAL_STATE_REL).exists(),
        "cookie_dbs": [str(p.relative_to(root)) for p in dbs],
        "cookie_db_locked": [str(p.relative_to(root)) for p in dbs if _db_locked(p)],
        "cdp_port": port,
        "cdp_available": cdp_available(cdp_port),
    }
    if checks["cookie_dbs"] and (checks["local_state_exists"] or not IS_WINDOWS):
        try:
            load_cookie_header(root, cdp_port=cdp_port)
            checks["session_ok"] = True
        except DoubaoCookieError as exc:
            checks["session_ok"] = False
            checks["session_error"] = str(exc)
    else:
        checks["session_ok"] = False
        checks["session_error"] = "Cookie 库或 Local State 缺失"
    return checks
