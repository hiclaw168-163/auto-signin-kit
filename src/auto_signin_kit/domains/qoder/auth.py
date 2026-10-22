"""Qoder CN 本地登录态: 目录发现 / DPAPI+GCM 解锁 / 令牌有效期。

数据目录形如 ``%APPDATA%\\com.qodercn.app*``;凭据文件 ``auth.v1.dat`` 的密钥
在 ``Local State`` 里,DPAPI + AES-256-GCM 双层保护。脚本不碰 refresh token,
不与桌面端争抢刷新令牌轮换;令牌过期就让用户打开桌面端重新登录。
"""

from __future__ import annotations

import base64
import glob
import json
import os
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from auto_signin_kit.core.adapters.platform_info import IS_WINDOWS
from auto_signin_kit.core.adapters.win_dpapi import DecryptError, decrypt_gcm, unprotect

AUTH_DIR_GLOB = "com.qodercn.app*"
AUTH_FILE = "auth.v1.dat"
LOCAL_STATE_FILE = "Local State"

#: 活动固定按 UTC+8 刷新,与运行机器的本地时区无关。
TZ_CN = timezone(timedelta(hours=8))


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


def find_auth_dirs() -> list[Path]:
    """扫描 Qoder CN 数据目录(QODER_AUTH_DIR 覆盖;多目录按最近修改排序)。"""
    override = os.environ.get("QODER_AUTH_DIR")
    if override:
        return [Path(override).expanduser()]
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    try:
        roots = sorted(
            glob.glob(str(appdata / AUTH_DIR_GLOB)),
            key=lambda p: Path(p).stat().st_mtime,
            reverse=True,
        )
    except OSError:
        roots = []
    return [Path(r) for r in roots]


def is_installed() -> bool:
    """客户端是否就绪: ``QODER_TOKEN`` 可直连,或数据目录存在(已装且启动过)。

    自动读取仅 Windows(见 README);非 Windows 无令牌时数据目录也找不到,
    返回 False 是如实的——调度注册探测据此跳过跑不起来的任务。
    """
    if os.environ.get("QODER_TOKEN"):
        return True
    return any(root.is_dir() for root in find_auth_dirs())


def _parse_iso(value: object) -> datetime | None:
    """解析 ISO8601 时间串;无时区视为 UTC。"""
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def token_expired(sess: dict) -> bool:
    """``expiresAt`` 已过期(过去)返回 True。"""
    expires = _parse_iso(sess.get("expiresAt"))
    return expires is not None and expires <= datetime.now(UTC)


def _read_store(root: Path) -> dict:
    raw = (root / AUTH_FILE).read_bytes()
    if raw[:3] != b"v10":
        raise AuthFormatError("%s 格式未知（%r）" % (AUTH_FILE, raw[:3]))
    if len(raw) < 3 + 12 + 16 + 2:
        raise AuthFormatError("%s 过短，可能正被客户端改写" % AUTH_FILE)
    state = json.loads((root / LOCAL_STATE_FILE).read_text(encoding="utf-8"))
    crypt = state.get("os_crypt") if isinstance(state, dict) else None
    if not isinstance(crypt, dict):
        raise AuthFormatError("%s 缺少 os_crypt 配置" % LOCAL_STATE_FILE)
    key_blob = base64.b64decode(crypt.get("encrypted_key") or "")
    if key_blob[:5] != b"DPAPI":
        raise AuthFormatError("%s 的 os_crypt 结构变了" % LOCAL_STATE_FILE)
    key = unprotect(key_blob[5:])
    plain = decrypt_gcm(key, raw[3:15], raw[15:])
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
    assert last is not None
    if isinstance(last, (AuthFormatError, DecryptError)):
        raise AuthFormatError(str(last))
    raise AuthFormatError("%s 读取失败（%s: %s）" % (AUTH_FILE, type(last).__name__, last))


def load_session() -> dict:
    """读出可用登录态(自动读取目前只支持 Windows);失败给带 looked_in 的错误。"""
    if not IS_WINDOWS:
        raise NoAuthError("自动读取 Qoder CN 登录态只支持 Windows；其他平台请设置 QODER_TOKEN")
    dirs = find_auth_dirs()
    looked_in = [str(d) for d in dirs]
    if not dirs:
        raise NoAuthError(
            "没找到 %%APPDATA%%\\%s" % AUTH_DIR_GLOB + "，请先安装并登录 Qoder CN 桌面端", looked_in
        )
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
            "Qoder CN 登录态读取失败（%s），请重新登录桌面端后重试" % detail, looked_in
        )
    raise NoAuthError("Qoder CN 桌面端尚未登录（%s）" % detail, looked_in)


def resolve_token() -> tuple[str, dict | None]:
    """令牌来源: 环境变量优先;否则解本地登录态。"""
    token = (os.environ.get("QODER_TOKEN") or "").strip()
    if token:
        return token, None
    sess = load_session()
    return sess["token"], sess
