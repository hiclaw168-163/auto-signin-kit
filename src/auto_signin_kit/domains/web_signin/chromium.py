"""chromium_cookies 适配器: 从客户端 Chromium 数据目录解会话 Cookie。

客户端类声明式应用(``kind: client`` + ``auth.kind: chromium_cookies``)
的登录态在本地客户端的 Chromium Cookie 库里。适配器把「数据目录 → 密钥
→ 解密 → Cookie 头」整条链路关在这里,引擎请求层只消费 :class:`Credential`。

- Windows: 标准 os_crypt——``Local State`` 的 ``os_crypt.encrypted_key``
  (DPAPI 包裹)解出 AES-256 密钥,v10 值走 AES-256-GCM;
- macOS: Keychain 条目(声明 ``keychain_service``,如 ``<App> Safe Storage``)
  取口令,PBKDF2-HMAC-SHA1(1003 轮,盐 ``saltysalt``)派生 16 字节密钥,
  v10/v11 值走 AES-128-CBC(IV 为 16 个空格);
- Linux: 固定口令 ``peanuts`` 同 PBKDF2(1 轮)派生,余同 macOS。

豆包客户端的 mac 加密是魔改 os_crypt(密文带应用层头,黑盒穷尽失败),
不在本适配器能力内——doubao 域走 CDP 兜底,不迁声明式(见 doubao/cookies.py)。
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import glob
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

from auto_signin_kit.core.adapters.mac_keychain import KeychainError, read_password
from auto_signin_kit.core.adapters.platform_info import IS_MACOS, IS_WINDOWS
from auto_signin_kit.core.adapters.win_dpapi import (
    DecryptError,
    decrypt_cbc,
    decrypt_gcm,
    unprotect,
)
from auto_signin_kit.domains.web_signin.authkit import (
    AuthBrokenError,
    Credential,
    NoAuthError,
)
from auto_signin_kit.domains.web_signin.schema import AppDeclaration

if IS_WINDOWS:  # 布局分支,import 时定死(测试可 monkeypatch 模块常量)
    COOKIES_REL = Path("Default") / "Network" / "Cookies"
    _PROFILES_GLOB = "Profile */Network/Cookies"
else:
    COOKIES_REL = Path("Default") / "Cookies"
    _PROFILES_GLOB = "Profile */Cookies"
LOCAL_STATE_REL = Path("Local State")

#%% ---------------- 密钥与解密(按平台分派) ----------------

_MAC_SALT = b"saltysalt"
_MAC_ITERATIONS = 1003
_LINUX_KEY = hashlib.pbkdf2_hmac("sha1", b"peanuts", _MAC_SALT, 1, dklen=16)
_CBC_IV = b" " * 16  # 标准 Chromium 的 CBC IV 是 16 个空格,不是玄学


def _win_key(root: Path) -> bytes:
    """Local State → DPAPI 解出 os_crypt 的 AES-256 密钥(仅当前用户可解)。"""
    try:
        data = json.loads((root / LOCAL_STATE_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuthBrokenError("Local State 不可读: %s (%s)"
                              % (root / LOCAL_STATE_REL, exc)) from exc
    wrapped = (data.get("os_crypt") or {}).get("encrypted_key")
    if not wrapped:
        raise AuthBrokenError("Local State 缺少 os_crypt.encrypted_key")
    raw = base64.b64decode(wrapped)
    if raw[:5] != b"DPAPI":
        raise AuthBrokenError("encrypted_key 前缀异常(非 DPAPI)")
    try:
        return unprotect(raw[5:])
    except DecryptError as exc:
        raise AuthBrokenError("DPAPI 解密密钥失败(须以登录客户端的同一"
                              " Windows 用户运行): %s" % exc) from exc


def _mac_key(service: str) -> bytes:
    """Keychain 口令 → PBKDF2 派生 AES-128 密钥;条目不存在如实报错。

    Chromium 的 Safe Storage 条目 account 为空串,经 core/adapters/
    mac_keychain 统一封装(域内不直接 subprocess,架构门禁约束)。
    """
    if not service:
        raise AuthBrokenError("macOS 需要 auth.keychain_service(Keychain 条目名)")
    try:
        out = read_password(service, "")
    except KeychainError as exc:
        raise AuthBrokenError("Keychain 条目 %r 读不到: %s" % (service, exc)) from exc
    return hashlib.pbkdf2_hmac("sha1", out.encode("utf-8"),
                               _MAC_SALT, _MAC_ITERATIONS, dklen=16)


def _aes_key(root: Path, decl: AppDeclaration) -> bytes:
    """当前平台的 os_crypt 密钥;平台不支持显式报错,不做静默兜底。"""
    if IS_WINDOWS:
        return _win_key(root)
    if IS_MACOS:
        return _mac_key(decl.auth.keychain_service)
    return _LINUX_KEY  # Linux: 标准 Chromium 固定口令派生


def _decrypt_value(key: bytes, encrypted: bytes, prefix_bytes: int) -> str | None:
    """解单个 cookie 值;非 v10/v11/解密失败返回 None(跳过,不中断整批)。"""
    version, nonce, blob = encrypted[:3], encrypted[3:15], encrypted[15:]
    try:
        if version == b"v10" and IS_WINDOWS:
            plain = decrypt_gcm(key, nonce, blob)
        elif version in (b"v10", b"v11"):
            plain = decrypt_cbc(key, _CBC_IV, encrypted[3:])
        else:
            return None
    except DecryptError:
        return None
    if len(plain) <= prefix_bytes:
        return None
    value = plain[prefix_bytes:].decode("utf-8", "replace")
    return value if value and value.isprintable() else None


#%% ---------------- Cookie 库读取 ----------------

def _copy_locked_file(src: Path, dst: Path) -> None:
    """拷贝可能被运行中客户端独占持有的 Cookies 库(Windows 共享读,POSIX 直读)。"""
    if not IS_WINDOWS:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        return
    generic_read, share_all, open_existing = 0x80000000, 0x1 | 0x2 | 0x4, 3
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateFileW(str(src), generic_read, share_all, None,
                                  open_existing, 0, None)
    if handle in (-1, 0xFFFFFFFFFFFFFFFF):
        # WinError 32 = 客户端运行中独占持有: 登录态此刻只在内存里,文件通道读不到
        raise AuthBrokenError("Cookie 库被运行中的客户端独占锁定(WinError %s): %s"
                              % (ctypes.get_last_error(), src))
    try:
        size = kernel32.SetFilePointer(handle, 0, None, 2)
        kernel32.SetFilePointer(handle, 0, None, 0)
        buffer = ctypes.create_string_buffer(size)
        read = wt.DWORD()
        if not kernel32.ReadFile(handle, buffer, size, ctypes.byref(read), None):
            raise AuthBrokenError("Cookie 库读取失败: %s" % src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(buffer.raw[: read.value])
    finally:
        kernel32.CloseHandle(handle)


def candidate_dbs(dirs: list[Path]) -> list[Path]:
    """全部候选 Cookie 库,按修改时间新者优先(新会话赢旧会话)。"""
    out: list[Path] = []
    for root in dirs:
        out.append(root / COOKIES_REL)
        out.extend(sorted(root.glob(_PROFILES_GLOB),
                          key=lambda p: p.stat().st_mtime, reverse=True))
    return [p for p in out if p.exists()]


def _read_db(db_path: Path, key: bytes, prefix: int, domain: str) -> dict[str, str]:
    """读单库并解密;host 含 domain 子串的行才收(域级'.'开头 cookie 优先)。"""
    dst = Path(tempfile.gettempdir()) / "auto-signin-kit-chromium-cookies.db"
    _copy_locked_file(db_path, dst)
    try:
        conn = sqlite3.connect(str(dst))
        try:
            if domain:
                rows = conn.execute(
                    "select host_key, name, encrypted_value from cookies"
                    " where host_key like ?", ("%" + domain,)).fetchall()
            else:
                rows = conn.execute(
                    "select host_key, name, encrypted_value from cookies").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise AuthBrokenError("Cookie 库打开失败: %s (%s)" % (db_path, exc)) from exc
    finally:
        dst.unlink(missing_ok=True)
    decrypted = [(h, n, v) for h, n, e in rows
                 if (v := _decrypt_value(key, bytes(e), prefix))]
    pairs: dict[str, str] = {}
    for prefer_dot in (True, False):
        for host, name, value in decrypted:
            if name in pairs or host.startswith(".") != prefer_dot:
                continue
            pairs[name] = value
    return pairs


#%% ---------------- 适配器契约三函数 ----------------

def _dirs(decl: AppDeclaration, ref: object) -> list[Path]:
    """数据目录候选: 具名账户 auth_dir 显式指定,否则声明 files(~ 展开,glob)。"""
    explicit = getattr(ref, "auth_dir", None) if ref is not None else None
    patterns = [explicit] if explicit else decl.auth.files
    out: list[Path] = []
    for pattern in patterns:
        expanded = str(Path(pattern).expanduser())
        for hit in glob.glob(expanded) or [expanded]:
            path = Path(hit)
            if path.is_dir() and path not in out:
                out.append(path)
    return out


def cookie_header(decl: AppDeclaration, ref: object = None) -> tuple[str, str]:
    """解出会话 Cookie 并拼请求头;返回 (Cookie 头, 来源目录)。

    失败按语义抛 NoAuthError(目录/库不存在)/AuthBrokenError(锁死/密钥
    不可解/必需 cookie 缺失);值本身不进任何日志或输出。
    """
    if not decl.auth.files:
        raise NoAuthError("auth.files 未声明客户端数据目录"
                          "（新建生成的桩声明请编辑 config/apps/<app_code>.yaml）")
    if not decl.auth.cookie_names:
        raise AuthBrokenError("auth.cookie_names 未声明必需 cookie 名"
                              "（新建生成的桩声明请编辑 config/apps/<app_code>.yaml）")
    names = decl.auth.cookie_names
    dirs = _dirs(decl, ref)
    if not dirs:
        raise NoAuthError("客户端数据目录不存在（探测: %s）" % ", ".join(decl.auth.files),
                          list(decl.auth.files))
    dbs = candidate_dbs(dirs)
    if not dbs:
        raise NoAuthError("Cookie 库不存在（客户端未安装或未登录过?）: %s"
                          % ", ".join(str(d) for d in dirs),
                          [str(d) for d in dirs])
    key = _aes_key(dirs[0], decl)
    merged: dict[str, str] = {}
    source = ""
    for db_path in dbs:
        try:
            pairs = _read_db(db_path, key, decl.auth.value_prefix_bytes,
                             decl.auth.cookie_domain)
        except AuthBrokenError:
            if IS_WINDOWS:  # POSIX 无强制锁,锁错误只在 Windows 有意义
                continue
            raise
        for name, value in pairs.items():
            merged.setdefault(name, value)
        if not source and pairs:
            source = str(db_path)
    missing = [n for n in names if n not in merged]
    if missing:
        raise AuthBrokenError(
            "登录态不完整: 缺 %s(解密成功但没有这些 cookie,磁盘登录态已过期?"
            "请登录客户端后重试)" % ",".join(missing))
    return "; ".join("%s=%s" % (n, merged[n]) for n in sorted(merged)), source


def _resolve(decl: AppDeclaration, ref: object) -> Credential:
    """适配器契约: 凭据解析 → Cookie 头注入 Credential.headers。"""
    header, source = cookie_header(decl, ref)
    headers = {"Cookie": header, **decl.auth.headers}
    return Credential(headers=headers, source=source)


def _ready(decl: AppDeclaration) -> bool:
    """就绪探测(不解密): 任一候选数据目录里存在 Cookie 库即视为就绪。"""
    return bool(candidate_dbs(_dirs(decl, None)))


def _doctor(decl: AppDeclaration, ref: object) -> tuple[dict, int]:
    """离线体检: 解出 Cookie 才算 OK;失败按语义给 NO_AUTH/AUTH_ERROR。"""
    try:
        header, source = cookie_header(decl, ref)
    except NoAuthError as exc:
        return {"result": "NO_AUTH", "needs_attention": True, "report": str(exc),
                "looked_in": exc.looked_in or None}, 2
    except AuthBrokenError as exc:
        return {"result": "AUTH_ERROR", "needs_attention": True, "report": str(exc),
                "looked_in": exc.looked_in or None}, 1
    found = [part.split("=", 1)[0] for part in header.split("; ")]
    return {"result": "OK", "needs_attention": False, "token_source": "cookie_db",
            "online_checked": False, "source": source, "cookies": found,
            "report": "登录态可读（%s，cookie: %s）" % (source, ", ".join(found))}, 0


#%% ---------------- 适配器契约(模块级三函数,同 ss_aes 口径) ----------------

resolve = _resolve
doctor = _doctor
ready = _ready
