"""Trae CN 本地登录态: 数据目录发现 / SS_AES 信封解锁 / 令牌有效期。

凭据在 ``%APPDATA%\\Trae CN\\User\\globalStorage\\storage.json`` 的
``iCubeAuthInfo://<authProviderId>`` 键下(Trae CN = ``icube.cloudide``),
由桌面端 ``byteCrypto`` 的 SS_AES 方案保护,与 DPAPI 无关、系统无关:

- 信封 = base64: 头 6 字节 ``[116,99,5,16,0,0]`` + 内嵌随机 AES 主密钥 32 字节;
- 派生: ``SHA512(主密钥 + 盐表[Gle^zle])`` 的 SHA512 前 32 字节 = 前 16 AES 密钥 +
  后 16 IV;
- 体 = AES-128-CBC,明文前 64 字节是 SHA-512 校验和。

脚本不碰 refreshToken 也不触发续期;令牌过期就请用户打开桌面端重新登录。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from auto_signin_kit.core.adapters.platform_info import IS_LINUX, IS_MACOS
from auto_signin_kit.core.adapters.win_dpapi import DecryptError, decrypt_cbc
from auto_signin_kit.core.errors import AutoSignInKitError

STORAGE_REL = Path("User") / "globalStorage" / "storage.json"
AUTH_KEY_PREFIX = "iCubeAuthInfo://"
#: Aha 体系的数字设备号藏在键名里: ``iCubeAuthInfo://icube-dc:<16位数字>``。
#: 签到请求的 ``x-device-id`` 必须发这种纯数字设备号,发 UUID 形态的
#: devDeviceId 会触发 9074 风控(2026-10 取证)。
AHA_DEVICE_KEY_PREFIX = "iCubeAuthInfo://icube-dc:"
AHA_DEVICE_ID_MIN_DIGITS = 8
AHA_DEVICE_ID_MAX_DIGITS = 20
#: Trae CN(CN 区)桌面端 authProviderId;解析失败时回退任意 iCubeAuthInfo:// 键。
DEFAULT_PROVIDER = "icube.cloudide"

#%% byteCrypto SS_AES 协议常量(逆向自 Trae CN 桌面端 byteCrypto.js 逆向所得)。
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


class TraeAuthError(AutoSignInKitError):
    """登录态不可用。looked_in 用于诊断输出,不含令牌。"""

    exit_code = 1

    def __init__(self, message: str, looked_in: list[str] | None = None) -> None:
        """只传递固定、非敏感的 message 和探测路径列表。"""
        super().__init__(message)
        self.looked_in = list(looked_in or [])


class NoAuthError(TraeAuthError):
    """本机没有可用的登录态。"""

    exit_code = 2


class AuthFormatError(TraeAuthError):
    """登录态存在但读不出令牌。"""


def _sha(b: bytes) -> bytes:
    return hashlib.sha512(b).digest()


def _unwrap_envelope(raw: bytes) -> dict:
    """SS_AES 解一封信封并解析出 userInfo JSON。"""
    if raw[:6] != SS_AES_HEADER:
        raise AuthFormatError("登录态信封头不属于 SS_AES 方案（%r）" % raw[:6])
    master = raw[6:6 + MASTER_KEY_LEN]
    salt = bytes(_GLE[i] ^ _ZLE[i] for i in range(64))
    digest = _sha(_sha(master) + salt) + b"\x00" * 64
    aes_key, iv = digest[:SS_CIPHER_KEY_LEN], digest[SS_CIPHER_KEY_LEN:2 * SS_CIPHER_KEY_LEN]
    try:
        plain = decrypt_cbc(aes_key, iv, raw[6 + MASTER_KEY_LEN:])
    except DecryptError as exc:
        raise AuthFormatError("登录态 AES 解密失败（%s）" % exc) from exc
    if plain[:64] != _sha(plain[64:]):
        raise AuthFormatError("登录态校验和不匹配，可能被损坏或被桌面端更新")
    try:
        return json.loads(plain[64:].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AuthFormatError("解锁后的登录态不是合法 JSON（%s）" % type(exc).__name__) from None


def data_roaming_roots(override_dir: str | None = None) -> list[Path]:
    """各平台 Trae CN 桌面端数据目录;优先级: 显式覆盖 > TRAE_AUTH_DIR > 平台默认。"""
    override = override_dir or os.environ.get("TRAE_AUTH_DIR")
    if override:
        return [Path(override).expanduser()]
    home = Path.home()
    if IS_MACOS:
        base = home / "Library" / "Application Support"
    elif IS_LINUX:
        base = home / ".config"
    else:
        base = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    return [base / name for name in ROAMING_DIR_NAMES]


def _read_storage_file(root: Path) -> tuple[Path, dict]:
    """在数据目录下找 storage.json 并读取。"""
    storage = root / STORAGE_REL
    if not storage.is_file():
        raise FileNotFoundError(str(storage))
    return storage, json.loads(storage.read_text(encoding="utf-8"))


def _auth_info(storage: Path) -> dict:
    """从 storage.json 取登录态键并解封;provider 键缺失时回退任意同前缀键。"""
    data = json.loads(storage.read_text(encoding="utf-8"))
    preferred = AUTH_KEY_PREFIX + DEFAULT_PROVIDER
    key = preferred if preferred in data else next(
        (k for k in data if k.startswith(AUTH_KEY_PREFIX)), None)
    if not key:
        raise AuthFormatError("%s 里没有登录态键（%s…）" % (storage.name, AUTH_KEY_PREFIX))
    enc = data.get(key)
    if not isinstance(enc, str):
        raise AuthFormatError("%s 的 %s 内容不是 base64 字符串" % (storage.name, key))
    try:
        raw = base64.b64decode(enc)
    except (ValueError, TypeError) as exc:
        raise AuthFormatError("登录态键不是合法 base64（%s）" % type(exc).__name__) from exc
    info = _unwrap_envelope(raw)
    if not isinstance(info, dict):
        raise AuthFormatError("解锁后的登录态不是 JSON 对象")
    return info


def load_session(override_dir: str | None = None) -> dict:
    """读出可用登录态(TRAE_TOKEN 之外的自动路径,全平台可用)。

    失败给带 looked_in 的错误;字段 ``_storage`` 登记凭据文件路径供诊断。
    """
    errors: list[str] = []
    looked_in: list[str] = []
    saw_storage = False
    for root in data_roaming_roots(override_dir):
        looked_in.append(str(root / STORAGE_REL))
        storage = root / STORAGE_REL
        if not storage.is_file():
            errors.append("%s: 未安装或未登录" % root.name)
            continue
        saw_storage = True
        try:
            info = _auth_info(storage)
        except AuthFormatError as exc:
            errors.append("%s: %s" % (root.name, exc))
            continue
        except (OSError, ValueError) as exc:
            errors.append("%s: %s: %s" % (root.name, type(exc).__name__, exc))
            continue
        token = info.get("token")
        if not isinstance(token, str) or not token.strip():
            raise AuthFormatError("解密成功但登录态里没有 token", looked_in)
        info["_storage"] = str(storage)
        return info
    detail = "；".join(errors)
    if saw_storage:
        raise AuthFormatError(
            "Trae CN 登录态读取失败（%s），请打开桌面端重新登录后重试" % detail, looked_in
        )
    raise NoAuthError("本机没有找到 Trae CN 登录态（%s）" % detail, looked_in)


def device_meta(override_dir: str | None = None) -> dict:
    """从 storage.json 读设备侧信息(明文字段,与登录态解封无关)。

    给签到请求补设备头用(2026-10 契约: claim 缺 x-device-id 会被服务端以
    下单类业务码拒绝);读不到返回空 dict,调用方按 best-effort 处理。

    - ``aha_device_id``: 键名 ``iCubeAuthInfo://icube-dc:<数字>`` 里的 Aha
      数字设备号——签到请求 ``x-device-id`` 的首选(见 AHA_DEVICE_KEY_PREFIX 注);
    - ``device_id``: ``telemetry.devDeviceId``,UUID 形态,仅作回退;
    - ``app_version``: ``iCubeLastVersion``,用于 UA/版本头。

    带 3 次 × 2s 重试: 开机唤醒的补跑任务(实测 08:29 触发)会与 Trae IDE
    启动同时读这份文件,IDE 写入时短暂独占文件锁,撞上 PermissionError 不等
    就等于放弃设备头,claim 必然 9004——与 workbuddy 凭据文件同一对策。
    """
    for root in data_roaming_roots(override_dir):
        for attempt in range(3):
            try:
                _, data = _read_storage_file(root)
            except (FileNotFoundError, ValueError):
                break  # 文件不存在/内容非法: 重试无意义,换下一个候选目录
            except OSError:
                if attempt < 2:
                    time.sleep(2)
                    continue
                break  # 锁持续占着: 不无限等,降级为无设备头(best-effort)
            return {
                "aha_device_id": aha_device_id(data),
                "device_id": data.get("telemetry.devDeviceId"),
                "app_version": data.get("iCubeLastVersion"),
            }
    return {}


def aha_device_id(storage: dict) -> str | None:
    """解析 storage.json 顶层键名里的 Aha 数字设备号;解析不到返回 None。

    设备号是键名的一部分(``iCubeAuthInfo://icube-dc:<数字>``,值为该设备的
    加密登录态),校验 8-20 位纯数字。**找不到不做随机伪造**——静默编造设备
    身份违反项目"配置或输入非法就显式报错,不做静默兜底"的硬规则,回退行为
    (用 UUID 形态的 devDeviceId)由调用方决定,9074 时结果里可见可诊断。
    """
    for key in storage:
        if not key.startswith(AHA_DEVICE_KEY_PREFIX):
            continue
        candidate = key[len(AHA_DEVICE_KEY_PREFIX):].strip()
        length_ok = AHA_DEVICE_ID_MIN_DIGITS <= len(candidate) <= AHA_DEVICE_ID_MAX_DIGITS
        if candidate.isdigit() and length_ok:
            return candidate
    return None


def _parse_iso(value: object) -> datetime | None:
    """解析 ISO8601 时间串;无时区视为 UTC。"""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def token_expired(info: dict) -> bool:
    """``expiredAt`` 已过期返回 True;缺字段视为未过期。"""
    expires = _parse_iso(info.get("expiredAt"))
    return expires is not None and expires <= datetime.now(UTC)


def resolve_token(override_dir: str | None = None) -> tuple[str, dict | None]:
    """令牌来源: 环境变量 TRAE_TOKEN 优先;否则解本地登录态。"""
    token = (os.environ.get("TRAE_TOKEN") or "").strip()
    if token:
        return token, None
    info = load_session(override_dir)
    return info["token"], info
