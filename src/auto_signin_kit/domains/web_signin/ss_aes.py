"""ss_aes 适配器: Trae 桌面端登录态信封的解锁与设备头(Trae 专用 kind)。

凭据在数据目录 ``User/globalStorage/storage.json`` 的
``iCubeAuthInfo://<authProviderId>`` 键下(Trae CN = ``icube.cloudide``),
由桌面端 ``byteCrypto`` 的 SS_AES 方案保护,与 DPAPI 无关、系统无关:

- 信封 = base64: 头 6 字节 ``[116,99,5,16,0,0]`` + 内嵌随机 AES 主密钥 32 字节;
- 派生: ``SHA512(主密钥 + 盐表[Gle^zle])`` 的 SHA512 前 32 字节 = 前 16 AES 密钥 +
  后 16 IV;
- 体 = AES-128-CBC,明文前 64 字节是 SHA-512 校验和。

设备头契约(2026-10-21 取证): ``x-device-id`` 首选键名
``iCubeAuthInfo://icube-dc:<数字>`` 里的 Aha 数字设备号(UUID 形态的
devDeviceId 会触发 9074 风控),回退 ``telemetry.devDeviceId``;UA 与
版本头取 ``iCubeLastVersion``。脚本不碰 refreshToken 也不触发续期。
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from auto_signin_kit.core.adapters.platform_info import IS_LINUX, IS_MACOS
from auto_signin_kit.core.adapters.win_dpapi import DecryptError, decrypt_cbc
from auto_signin_kit.core.kit.tokens import token_expired
from auto_signin_kit.domains.web_signin.authkit import (
    AuthBrokenError,
    AuthRejectedError,
    Credential,
    NoAuthError,
)
from auto_signin_kit.domains.web_signin.schema import AppDeclaration

STORAGE_REL = Path("User") / "globalStorage" / "storage.json"
AUTH_KEY_PREFIX = "iCubeAuthInfo://"
#: Aha 体系的数字设备号藏在键名里: ``iCubeAuthInfo://icube-dc:<16位数字>``。
AHA_DEVICE_KEY_PREFIX = "iCubeAuthInfo://icube-dc:"
AHA_DEVICE_ID_MIN_DIGITS = 8
AHA_DEVICE_ID_MAX_DIGITS = 20
#: Trae CN(CN 区)桌面端 authProviderId;解析失败时回退任意 iCubeAuthInfo:// 键。
DEFAULT_PROVIDER = "icube.cloudide"

#%% byteCrypto SS_AES 协议常量(逆向自 Trae CN 桌面端 byteCrypto.js)。
SS_AES_HEADER = bytes([116, 99, 5, 16, 0, 0])
MASTER_KEY_LEN = 32  # 内嵌随机主密钥长度(其后才是 AES 密文)
SS_CIPHER_KEY_LEN = 16
_GLE = bytes([82, 9, 106, 213, 48, 54, 165, 56, 191, 64, 163, 158, 129, 243, 215, 251,
              124, 227, 57, 130, 155, 47, 255, 135, 52, 142, 67, 68, 196, 222, 233,
              203, 84, 123, 148, 50, 166, 194, 35, 61, 238, 76, 149, 11, 66, 250,
              195, 78, 8, 46, 161, 102, 40, 217, 36, 178, 118, 91, 162, 73, 109,
              139, 209, 37])
_ZLE = bytes([31, 221, 168, 51, 136, 7, 199, 49, 177, 18, 16, 89, 39, 128, 236, 95,
              96, 81, 127, 169, 25, 181, 74, 13, 45, 229, 122, 159, 147, 201, 156,
              239, 160, 224, 59, 77, 174, 42, 245, 176, 200, 235, 187, 60, 131, 83,
              153, 97, 23, 43, 4, 126, 186, 119, 214, 38, 225, 105, 20, 99, 85, 33,
              12, 125])

ROAMING_DIR_NAMES = ("Trae CN", "Trae")


def _sha(b: bytes) -> bytes:
    return hashlib.sha512(b).digest()


def _unwrap_envelope(raw: bytes) -> dict:
    """SS_AES 解一封信封并解析出 userInfo JSON。"""
    if raw[:6] != SS_AES_HEADER:
        raise AuthBrokenError("登录态信封头不属于 SS_AES 方案（%r）" % raw[:6])
    master = raw[6:6 + MASTER_KEY_LEN]
    salt = bytes(_GLE[i] ^ _ZLE[i] for i in range(64))
    digest = _sha(_sha(master) + salt) + b"\x00" * 64
    aes_key, iv = digest[:SS_CIPHER_KEY_LEN], digest[SS_CIPHER_KEY_LEN:2 * SS_CIPHER_KEY_LEN]
    try:
        plain = decrypt_cbc(aes_key, iv, raw[6 + MASTER_KEY_LEN:])
    except DecryptError as exc:
        raise AuthBrokenError("登录态 AES 解密失败（%s）" % exc) from exc
    if plain[:64] != _sha(plain[64:]):
        raise AuthBrokenError("登录态校验和不匹配，可能被损坏或被桌面端更新")
    try:
        return json.loads(plain[64:].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AuthBrokenError("解锁后的登录态不是合法 JSON（%s）" % type(exc).__name__) from None


def data_roaming_roots(override_dirs: list[str] | None = None,
                       auth_dir: str | None = None) -> list[Path]:
    """数据目录候选: 显式声明/账户 auth_dir > 平台默认(Trae CN、Trae)。"""
    if auth_dir:
        return [Path(auth_dir).expanduser()]
    if override_dirs:
        return [Path(p).expanduser() for p in override_dirs]
    home = Path.home()
    if IS_MACOS:
        base = home / "Library" / "Application Support"
    elif IS_LINUX:
        base = home / ".config"
    else:
        import os

        base = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    return [base / name for name in ROAMING_DIR_NAMES]


def aha_device_id(storage: dict) -> str | None:
    """解析 storage.json 顶层键名里的 Aha 数字设备号;解析不到返回 None。

    **找不到不做随机伪造**——静默编造设备身份违反"配置或输入非法就显式
    报错"的硬规则;回退行为(UUID 形态 devDeviceId)由调用方决定。
    """
    for key in storage:
        if not key.startswith(AHA_DEVICE_KEY_PREFIX):
            continue
        candidate = key[len(AHA_DEVICE_KEY_PREFIX):].strip()
        length_ok = AHA_DEVICE_ID_MIN_DIGITS <= len(candidate) <= AHA_DEVICE_ID_MAX_DIGITS
        if candidate.isdigit() and length_ok:
            return candidate
    return None


def _read_storage(root: Path) -> tuple[Path, dict]:
    """读数据目录下的 storage.json(带 3×2s 重试: 开机补跑会与 IDE 抢文件锁)。"""
    storage = root / STORAGE_REL
    for attempt in range(3):
        try:
            return storage, json.loads(storage.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except ValueError:
            raise
        except OSError:
            if attempt < 2:
                time.sleep(2)
                continue
            raise
    raise FileNotFoundError(str(storage))


def _auth_info(storage: Path) -> dict:
    """从 storage.json 取登录态键并解封;provider 键缺失时回退任意同前缀键。"""
    data = json.loads(storage.read_text(encoding="utf-8"))
    preferred = AUTH_KEY_PREFIX + DEFAULT_PROVIDER
    key = preferred if preferred in data else next(
        (k for k in data if k.startswith(AUTH_KEY_PREFIX)), None)
    if not key:
        raise AuthBrokenError("%s 里没有登录态键（%s…）" % (storage.name, AUTH_KEY_PREFIX))
    enc = data.get(key)
    if not isinstance(enc, str):
        raise AuthBrokenError("%s 的 %s 内容不是 base64 字符串" % (storage.name, key))
    try:
        raw = base64.b64decode(enc)
    except (ValueError, TypeError) as exc:
        raise AuthBrokenError("登录态键不是合法 base64（%s）" % type(exc).__name__) from exc
    info = _unwrap_envelope(raw)
    if not isinstance(info, dict):
        raise AuthBrokenError("解锁后的登录态不是 JSON 对象")
    return info


def _session_roots(decl: AppDeclaration, ref: Any) -> list[Path]:
    auth_dir = ref.auth_dir if (ref is not None and getattr(ref, "auth_dir", None)) else None
    return data_roaming_roots(decl.auth.files or None, auth_dir)


def resolve(decl: AppDeclaration, ref: Any) -> Credential:
    """解封登录态 → 令牌 + 设备头;失败按语义抛 NoAuth/AuthBroken/AuthRejected。"""
    errors: list[str] = []
    looked_in: list[str] = []
    saw_storage = False
    for root in _session_roots(decl, ref):
        looked_in.append(str(root / STORAGE_REL))
        if not (root / STORAGE_REL).is_file():
            errors.append("%s: 未安装或未登录" % root.name)
            continue
        saw_storage = True
        try:
            info = _auth_info(root / STORAGE_REL)
        except AuthBrokenError as exc:
            errors.append("%s: %s" % (root.name, exc))
            continue
        except (OSError, ValueError) as exc:
            errors.append("%s: %s: %s" % (root.name, type(exc).__name__, exc))
            continue
        token = info.get("token")
        if not isinstance(token, str) or not token.strip():
            raise AuthBrokenError("解密成功但登录态里没有 token", looked_in)
        if token_expired(info):
            raise AuthRejectedError("登录态已于 %s 过期，请打开桌面端重新登录"
                               % info.get("expiredAt"), looked_in)
        return _credential(decl, token, info, str(root / STORAGE_REL))
    detail = "；".join(errors)
    if saw_storage:
        raise AuthBrokenError("登录态读取失败（%s），请打开桌面端重新登录后重试"
                              % detail, looked_in)
    raise NoAuthError("本机没有找到登录态（%s）" % detail, looked_in)


def _credential(decl: AppDeclaration, token: str, info: dict, source: str) -> Credential:
    """令牌 + 设备头 + 静态追加头组装(请求头对齐客户端指纹)。"""
    # 设备侧信息读明文字段(与解封无关);读不到降级为无设备头(best-effort)
    device: dict = {}
    for root in _session_roots(decl, None):
        try:
            _, data = _read_storage(root)
        except (FileNotFoundError, OSError, ValueError):
            continue
        device = {"aha": aha_device_id(data),
                  "id": data.get("telemetry.devDeviceId"),
                  "version": data.get("iCubeLastVersion")}
        break
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    header = decl.auth.token_header
    if header is not None:
        headers[header.name] = header.prefix + token
    device_id = device.get("aha") or device.get("id")
    if device_id:
        headers["x-device-id"] = str(device_id)
    if device.get("version"):
        headers["x-app-version"] = str(device["version"])
        headers["User-Agent"] = "Trae/%s" % device["version"]
    headers.update(decl.auth.headers)
    return Credential(token=token, headers=headers, expires_at=info.get("expiredAt"),
                      source=source)


def doctor(decl: AppDeclaration, ref: Any) -> tuple[dict, int]:
    """离线体检: 登录态可读性与有效期;不联网、不领取。"""
    try:
        cred = resolve(decl, ref)
    except NoAuthError as exc:
        return {"result": "NO_AUTH", "needs_attention": True, "report": str(exc),
                "looked_in": exc.looked_in or None}, 2
    except AuthRejectedError as exc:
        return {"result": "AUTH_REJECTED", "needs_attention": True, "report": str(exc),
                "looked_in": exc.looked_in or None}, 1
    except AuthBrokenError as exc:
        return {"result": "AUTH_ERROR", "needs_attention": True, "report": str(exc),
                "looked_in": exc.looked_in or None}, 1
    expires_text = str(cred.expires_at or "")[:16]
    report = "登录态可读（%s）" % cred.source
    if expires_text:
        report += "，有效期至 %s" % expires_text
    return {"result": "OK", "needs_attention": False, "token_source": "desktop",
            "online_checked": False, "source": cred.source,
            "token_expires_at": cred.expires_at, "report": report}, 0


def ready(decl: AppDeclaration) -> bool:
    """数据目录存在即视为就绪(不解锁 Keychain/信封,不打扰用户)。"""
    return any((root / STORAGE_REL).is_file()
               for root in data_roaming_roots(decl.auth.files or None))
