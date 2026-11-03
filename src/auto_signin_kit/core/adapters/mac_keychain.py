"""macOS 凭据解锁: Keychain 取密码 + Chromium mac os_crypt V10(AES-128-CBC)。

对齐 Electron ``safeStorage`` 在 macOS 上的实际链路: 对称密钥存 Keychain
(``security find-generic-password`` 可读,已实测无授权弹窗),加密格式与
Chromium cookie 同源——``v10`` 前缀 + AES-128-CBC,密钥由密码经
PBKDF2-HMAC-SHA1(salt="saltysalt", 1003 轮) 派生 16 字节,IV 固定 16 个空格,
PKCS7 填充,无 HMAC(完整性由 JSON 解析与调用方校验兜底)。

注意与 Windows 版不同: Windows 是 DPAPI 包密钥 + AES-256-GCM(见 win_dpapi)。
"""

from __future__ import annotations

import subprocess

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from auto_signin_kit.core.errors import AdapterError

#: Chromium mac os_crypt 的固定派生参数(与 cookie 加密同源,勿"修正")。
_SALT = b"saltysalt"
_ITERATIONS = 1003
_KEY_LEN = 16
_IV = b" " * 16


class KeychainError(AdapterError):
    """Keychain 读取失败(条目不存在/拒绝访问/非 macOS 环境)。"""


def read_password(service: str, account: str) -> str:
    """读 Keychain 通用密码条目;条目缺失或被拒时显式报错。"""
    proc = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],  # noqa: S607 — macOS 固定系统命令
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise KeychainError(
            "Keychain 条目读取失败(service=%s)：%s"
            % (service, (proc.stderr or "").strip() or "exit %d" % proc.returncode))
    return proc.stdout.strip()


def decrypt_chrome_mac_v10(raw: bytes, password: str) -> bytes:
    """解开 Chromium mac os_crypt V10 数据(前缀 v10 + AES-128-CBC)。

    password 传 Keychain 里的**原始字符串**(实测不 base64 解码,直接 UTF-8
    字节参与派生)。格式或填充非法抛 AdapterError,由调用方转译为域内错误。
    """
    if raw[:3] != b"v10":
        raise AdapterError("数据前缀不是 v10（%r）" % raw[:3])
    key = PBKDF2HMAC(algorithm=hashes.SHA1(), length=_KEY_LEN, salt=_SALT,  # noqa: S303 — Chromium v10 协议即 PBKDF2-SHA1,必须对齐
                     iterations=_ITERATIONS).derive(password.encode("utf-8"))
    decryptor = Cipher(algorithms.AES(key), modes.CBC(_IV)).decryptor()
    padded = decryptor.update(raw[3:]) + decryptor.finalize()
    pad = padded[-1] if padded else 0
    if not 1 <= pad <= 16 or padded[-pad:] != bytes([pad]) * pad:
        raise AdapterError("PKCS7 填充非法（密钥不对或数据损坏）")
    return padded[:-pad]


def encrypt_chrome_mac_v10(plain: bytes, password: str) -> bytes:
    """反向构造 V10 数据,仅供测试与工具使用(生产只读不写)。"""
    key = PBKDF2HMAC(algorithm=hashes.SHA1(), length=_KEY_LEN, salt=_SALT,  # noqa: S303 — 同上,对齐 Chromium v10 协议
                     iterations=_ITERATIONS).derive(password.encode("utf-8"))
    pad = 16 - len(plain) % 16
    padded = plain + bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(key), modes.CBC(_IV)).encryptor()
    return b"v10" + encryptor.update(padded) + encryptor.finalize()
