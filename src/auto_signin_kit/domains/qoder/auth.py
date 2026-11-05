"""Qoder 本地登录态: 目录发现 / 双平台解锁 / 令牌有效期 / API 域推导。

凭据文件 ``auth.v1.dat`` 两平台同名但解锁链不同(2026-11-02 mac 实测):

- Windows: 数据目录 ``%APPDATA%\\com.qodercn.app*``;密钥在 ``Local State``
  的 ``os_crypt.encrypted_key``,DPAPI + AES-256-GCM(见 win_dpapi)
- macOS: 数据目录 ``~/Library/Application Support/com.qoder.app*``;无
  ``Local State``,密钥在 Keychain 条目(service=Qoder App Safe Storage),
  PBKDF2(saltysalt) 派生后 AES-128-CBC(见 mac_keychain)

CN 版与国际版客户端协议同构(2026-11-02 实测 openapi.qoder.sh 同一套
campaigns/claim 接口),只差 API 域;见 ``detect_api_base``。

脚本不碰 refresh token,不与桌面端争抢刷新令牌轮换;令牌过期就让用户
打开桌面端重新登录。
"""

from __future__ import annotations

import base64
import contextlib
import glob
import json
import os
import time
from datetime import timedelta, timezone
from pathlib import Path

from auto_signin_kit.core.adapters import mac_keychain
from auto_signin_kit.core.adapters.platform_info import IS_MACOS
from auto_signin_kit.core.adapters.win_dpapi import DecryptError, decrypt_gcm, unprotect
from auto_signin_kit.core.errors import AdapterError
from auto_signin_kit.core.kit.tokens import token_expired as _core_token_expired

AUTH_DIR_GLOB = "com.qodercn.app*"
AUTH_DIR_GLOB_MAC = "com.qoder.app*"
AUTH_FILE = "auth.v1.dat"
LOCAL_STATE_FILE = "Local State"

#: mac 客户端 Keychain 条目(Electron safeStorage 按 app.name 生成,实测固定)。
KEYCHAIN_SERVICE = "Qoder App Safe Storage"
KEYCHAIN_ACCOUNT = "Qoder App Key"

#: 活动固定按 UTC+8 刷新,与运行机器的本地时区无关。
TZ_CN = timezone(timedelta(hours=8))

#: 登录态签发主域 → API 域(国际版实测;CN 版按命名约定映射)。
_API_BASE_BY_ISSUER = {
    "qoder.com": "https://openapi.qoder.sh",
    "qoder.com.cn": "https://openapi.qoder.com.cn",
}


class QoderAuthError(Exception):
    """登录态不可用。looked_in 用于诊断输出,不含令牌。"""

    def __init__(self, message: str, looked_in: list[str] | None = None) -> None:
        """帮助构造;只传递固定、非敏感的 message 和探测路径列表。"""
        super().__init__(message)
        self.looked_in = list(looked_in or [])


class NoAuthError(QoderAuthError):
    """本机没有可用的登录态。"""


class AuthFormatError(QoderAuthError):
    """登录态存在但读不出令牌。"""


def _default_roots() -> list[Path]:
    """按平台返回数据目录扫描根(测试可替换);平台知识收敛在此。"""
    if IS_MACOS:
        return [Path.home() / "Library" / "Application Support"]
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return [appdata]


def find_auth_dirs() -> list[Path]:
    """扫描 Qoder 数据目录(QODER_AUTH_DIR 覆盖;多目录按最近修改排序)。

    Windows: ``%APPDATA%\\com.qodercn.app*``(CN 版);macOS: 国际版实测为
    ``~/Library/Application Support/com.qoder.app*``,CN mac 版按 bundle id
    命名惯例两模式都扫。
    """
    override = os.environ.get("QODER_AUTH_DIR")
    if override:
        return [Path(override).expanduser()]
    patterns = [AUTH_DIR_GLOB, AUTH_DIR_GLOB_MAC] if IS_MACOS else [AUTH_DIR_GLOB]
    try:
        hits: list[Path] = []
        for root in _default_roots():
            for pattern in patterns:
                hits.extend(Path(p) for p in glob.glob(str(root / pattern)))
        hits = list(dict.fromkeys(hits))
        hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return hits
    except OSError:
        return []


def is_installed() -> bool:
    """客户端是否就绪: ``QODER_TOKEN`` 可直连,或数据目录存在(已装且登录过)。"""
    if os.environ.get("QODER_TOKEN"):
        return True
    return any(root.is_dir() for root in find_auth_dirs())


def client_info() -> dict:
    """客户端档案(账户表登记用): 数据目录。不可得返回空 dict,如实上报。"""
    for root in find_auth_dirs():
        if root.is_dir():
            return {"data_dir": str(root)}
    return {}


def token_expired(sess: dict) -> bool:
    """``expiresAt`` 已过期(过去)返回 True;判定逻辑在 core.kit.tokens。"""
    return _core_token_expired(sess, "expiresAt")


def _read_store(root: Path) -> dict:
    raw = (root / AUTH_FILE).read_bytes()
    if raw[:3] != b"v10":
        raise AuthFormatError("%s 格式未知（%r）" % (AUTH_FILE, raw[:3]))
    if len(raw) < 3 + 12 + 16 + 2:
        raise AuthFormatError("%s 过短，可能正被客户端改写" % AUTH_FILE)
    plain = _decrypt_raw(raw, root)
    try:
        sess = json.loads(plain.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AuthFormatError("解锁后的会话不是合法 JSON（%s）" % type(exc).__name__) from None
    if not isinstance(sess, dict):
        raise AuthFormatError("会话内容不是 JSON 对象")
    token = sess.get("token")
    if not isinstance(token, str) or not token.strip():
        raise AuthFormatError("解密成功但会话里没有 token")
    return sess


def _decrypt_raw(raw: bytes, root: Path) -> bytes:
    """按平台解锁 auth.v1.dat(密文体从 v10 前缀之后)。"""
    if IS_MACOS:
        try:
            password = mac_keychain.read_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
        except AdapterError as exc:
            raise AuthFormatError("Keychain 密钥读取失败：%s" % exc) from exc
        try:
            return mac_keychain.decrypt_chrome_mac_v10(raw, password)
        except AdapterError as exc:
            raise AuthFormatError(str(exc)) from exc
    state = json.loads((root / LOCAL_STATE_FILE).read_text(encoding="utf-8"))
    crypt = state.get("os_crypt") if isinstance(state, dict) else None
    if not isinstance(crypt, dict):
        raise AuthFormatError("%s 缺少 os_crypt 配置" % LOCAL_STATE_FILE)
    key_blob = base64.b64decode(crypt.get("encrypted_key") or "")
    if key_blob[:5] != b"DPAPI":
        raise AuthFormatError("%s 的 os_crypt 结构变了" % LOCAL_STATE_FILE)
    key = unprotect(key_blob[5:])
    return decrypt_gcm(key, raw[3:15], raw[15:])


def _read_store_retry(root: Path, attempts: int = 3) -> dict:
    """客户端可能正在重写凭据,读到半截就短暂重试。"""
    last: Exception | None = None
    for index in range(attempts):
        try:
            return _read_store(root)
        except (ValueError, OSError, AuthFormatError, DecryptError) as exc:
            last = exc
            if index + 1 < attempts:
                time.sleep(0.25)
    if last is None:  # 理论不可达(循环必有赋值),防御 -O 裁剪不用 assert
        raise AuthFormatError("重试后未获得读取结果")
    if isinstance(last, (AuthFormatError, DecryptError)):
        raise AuthFormatError(str(last))
    raise AuthFormatError("%s 读取失败（%s: %s）" % (AUTH_FILE, type(last).__name__, last))


def load_session(auth_dir: str | None = None) -> dict:
    """读出可用登录态(Windows/macOS 双平台);失败给带 looked_in 的错误。

    ``auth_dir``(SPEC-12 P2): 具名账户的显式数据目录——只看这一个目录,
    不做全局扫描兜底(落错账户比没签到严重);目录不存在显式报错。
    缺省(None)走 ``find_auth_dirs()`` 全链路,行为不变。
    """
    if auth_dir:
        root = Path(auth_dir).expanduser()
        if not root.is_dir():
            raise NoAuthError("账户数据目录不存在：%s" % root, [str(root)])
        dirs = [root]
    else:
        dirs = find_auth_dirs()
    looked_in = [str(d) for d in dirs]
    if not dirs:
        where = ("~/Library/Application Support/" + AUTH_DIR_GLOB_MAC if IS_MACOS
                 else "%APPDATA%\\..." + AUTH_DIR_GLOB)
        raise NoAuthError("没找到 %s，请先安装并登录 Qoder 桌面端" % where, looked_in)
    errors: list[str] = []
    saw_auth_file = False
    for root in dirs:
        if not (root / AUTH_FILE).is_file():
            errors.append("%s: 未登录" % root.name)
            continue
        saw_auth_file = True
        try:
            sess = _read_store_retry(root)
        except AuthFormatError as exc:
            errors.append("%s: %s" % (root.name, exc))
            continue
        except OSError as exc:
            errors.append("%s: %s: %s" % (root.name, type(exc).__name__, exc))
            continue
        sess["_auth_dir"] = str(root)
        return sess
    detail = "；".join(errors)
    if saw_auth_file:
        raise AuthFormatError(
            "Qoder 登录态读取失败（%s），请重新登录桌面端后重试" % detail, looked_in
        )
    raise NoAuthError("Qoder 桌面端尚未登录（%s）" % detail, looked_in)


def detect_api_base(sess: dict | None) -> str | None:
    """从登录态 ``authenticationContext`` 推导 API 域;推导不出给 None。

    会话的 authenticationContext 形如 ``["https://qoder.com",
    "https://openapi.qoder.sh", <uuid>, "prod"]``(实测存的是 JSON 字符串,
    先反序列化): 优先直取其中的 ``openapi.*`` 域,没有就按主域映射。令牌
    只对签发它的端点有效——装哪个版本的客户端(CN/国际)就自动用哪个端点。
    """
    ctx = (sess or {}).get("authenticationContext")
    if isinstance(ctx, str):
        with contextlib.suppress(ValueError):
            ctx = json.loads(ctx)
    if not isinstance(ctx, list):
        return None
    hosts = [
        str(item).split("//", 1)[-1].rstrip("/")
        for item in ctx if isinstance(item, str) and "//" in item
    ]
    for host in hosts:
        if host.startswith("openapi."):
            return "https://" + host
    for host in hosts:
        base = _API_BASE_BY_ISSUER.get(host)
        if base:
            return base
    return None


def resolve_token(auth_dir: str | None = None) -> tuple[str, dict | None]:
    """令牌来源: 环境变量优先;否则解本地登录态。

    ``auth_dir``(SPEC-12 P2): 具名账户显式数据目录——此时**跳过**全局
    ``QODER_TOKEN`` 环境变量(那是 default 户的令牌,混用会拿 A 户令牌
    签 B 户的到),只信该目录里的登录态。
    """
    if auth_dir is None:
        token = (os.environ.get("QODER_TOKEN") or "").strip()
        if token:
            return token, None
    sess = load_session(auth_dir)
    return sess["token"], sess
