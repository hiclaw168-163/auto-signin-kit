"""Windows 凭据解锁: DPAPI(当前用户作用域) + AES-256-GCM(CNG,零第三方依赖)。

对齐 Qoder CN 桌面端 ``auth.v1.dat`` 的解锁方式: ``Local State`` 里 DPAPI 包住
的对称密钥,再用它 GCM 解开凭据文件。装了 ``cryptography`` 优先用,不在依赖里强求。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

from auto_signin_kit.core.errors import AdapterError


class DecryptError(AdapterError):
    """凭据解锁失败;调用方转译为域内 Auth 错误。"""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


class _AeadInfo(ctypes.Structure):  # BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO
    _fields_ = [
        ("cbSize", ctypes.c_ulong), ("dwInfoVersion", ctypes.c_ulong),
        ("pbNonce", ctypes.POINTER(ctypes.c_ubyte)), ("cbNonce", ctypes.c_ulong),
        ("pbAuthData", ctypes.POINTER(ctypes.c_ubyte)), ("cbAuthData", ctypes.c_ulong),
        ("pbTag", ctypes.POINTER(ctypes.c_ubyte)), ("cbTag", ctypes.c_ulong),
        ("pbMacContext", ctypes.POINTER(ctypes.c_ubyte)), ("cbMacContext", ctypes.c_ulong),
        ("pbAAD", ctypes.POINTER(ctypes.c_ubyte)), ("cbAAD", ctypes.c_ulonglong),
        ("cbData", ctypes.c_ulonglong), ("dwFlags", ctypes.c_ulong),
    ]


def unprotect(data: bytes) -> bytes:
    """DPAPI 解密(只限启动进程的同一 Windows 用户)。"""
    src = _DataBlob(len(data), ctypes.create_string_buffer(data, len(data)))
    dst = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(src), None, None, None, None, 0x01, ctypes.byref(dst)
    ):
        win_error = ctypes.WinError()
        raise DecryptError("DPAPI 解不开密钥（%s）——需要同一 Windows 用户且已登录桌面" % win_error)
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def _cng_cipher_op(key: bytes, iv: bytes, blob: bytes, *, encrypt: bool) -> bytes:
    """CNG AES-CBC 单块链式加解密(无认证属性);操作失败报 DecryptError。"""
    bcrypt = ctypes.WinDLL("bcrypt.dll")

    def chk(res: int, what: str) -> None:
        if res != 0:
            raise DecryptError("CNG %s 失败 NTSTATUS=0x%08x" % (what, res & 0xFFFFFFFF))

    h_alg = ctypes.c_void_p()
    h_key = ctypes.c_void_p()
    if len(blob) % 16 or not len(blob):
        raise DecryptError("AES-CBC 输入必须是 16 字节倍数且非空")
    try:
        chk(bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), "AES", None, 0),
            "OpenAlg")
        mode = "ChainingModeCBC"
        chk(bcrypt.BCryptSetProperty(h_alg, "ChainingMode", mode, (len(mode) + 1) * 2, 0),
            "SetProperty")
        kbuf = ctypes.create_string_buffer(key, len(key))
        chk(bcrypt.BCryptGenerateSymmetricKey(h_alg, ctypes.byref(h_key), None, 0,
                                              kbuf, len(key), 0), "GenKey")
        nbuf = (ctypes.c_ubyte * len(iv)).from_buffer_copy(iv)
        ibuf = ctypes.create_string_buffer(blob, len(blob))
        obuf = (ctypes.c_ubyte * len(blob))()
        done = ctypes.c_ulong()
        if encrypt:
            chk(bcrypt.BCryptEncrypt(h_key, ibuf, len(blob), None, nbuf, len(iv),
                                     obuf, len(blob), ctypes.byref(done), 0), "Encrypt")
        else:
            chk(bcrypt.BCryptDecrypt(h_key, ibuf, len(blob), None, nbuf, len(iv),
                                     obuf, len(blob), ctypes.byref(done), 0), "Decrypt")
        return bytes(obuf[:done.value])
    finally:
        if h_key:
            bcrypt.BCryptDestroyKey(h_key)
        if h_alg:
            bcrypt.BCryptCloseAlgorithmProvider(h_alg, 0)


def encrypt_cbc(key: bytes, iv: bytes, blob: bytes) -> bytes:
    """AES-CBC + PKCS7 填充加密;优先 cryptography,缺库退回 CNG(仅 Windows)。"""
    pad = 16 - len(blob) % 16
    blob = blob + bytes([pad]) * pad
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        return _cng_cipher_op(key, iv, blob, encrypt=True)
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return enc.update(blob) + enc.finalize()


def decrypt_cbc(key: bytes, iv: bytes, blob: bytes) -> bytes:
    """AES-CBC 原样解密(去 PKCS7 填充);优先 cryptography,缺库退回 CNG。"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        plain = _cng_cipher_op(key, iv, blob, encrypt=False)
    else:
        dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        plain = dec.update(blob) + dec.finalize()
    pad = plain[-1] if plain else 0
    if plain and 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
        return plain[:-pad]
    return plain


def _decrypt_gcm_cng(key: bytes, nonce: bytes, blob: bytes) -> bytes:
    """AES-GCM 解密,走 Windows 自带的 CNG,不依赖 cryptography。"""
    bcrypt = ctypes.WinDLL("bcrypt.dll")
    ct, tag = blob[:-16], blob[-16:]

    def chk(res: int, what: str) -> None:
        if res != 0:
            raise DecryptError("CNG %s 失败 NTSTATUS=0x%08x" % (what, res & 0xFFFFFFFF))

    h_alg = ctypes.c_void_p()
    h_key = ctypes.c_void_p()
    try:
        chk(bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), "AES", None, 0), "OpenAlg")
        mode = "ChainingModeGCM"
        chk(bcrypt.BCryptSetProperty(h_alg, "ChainingMode", mode, (len(mode) + 1) * 2, 0),
            "SetProperty")
        kbuf = ctypes.create_string_buffer(key, len(key))
        chk(bcrypt.BCryptGenerateSymmetricKey(h_alg, ctypes.byref(h_key), None, 0,
                                              kbuf, len(key), 0), "GenKey")
        nbuf = (ctypes.c_ubyte * len(nonce)).from_buffer_copy(nonce)
        tbuf = (ctypes.c_ubyte * len(tag)).from_buffer_copy(tag)
        info = _AeadInfo()
        info.cbSize = ctypes.sizeof(_AeadInfo)
        info.dwInfoVersion = 1
        info.pbNonce = ctypes.cast(nbuf, ctypes.POINTER(ctypes.c_ubyte))
        info.cbNonce = len(nonce)
        info.pbTag = ctypes.cast(tbuf, ctypes.POINTER(ctypes.c_ubyte))
        info.cbTag = len(tag)
        ibuf = ctypes.create_string_buffer(ct, len(ct))
        obuf = ctypes.create_string_buffer(len(ct))
        done = ctypes.c_ulong()
        chk(bcrypt.BCryptDecrypt(h_key, ibuf, len(ct), ctypes.byref(info), None, 0,
                                 obuf, len(ct), ctypes.byref(done), 0), "Decrypt")
        return obuf.raw[:done.value]
    finally:
        if h_key:
            bcrypt.BCryptDestroyKey(h_key)
        if h_alg:
            bcrypt.BCryptCloseAlgorithmProvider(h_alg, 0)


def decrypt_gcm(key: bytes, nonce: bytes, blob: bytes) -> bytes:
    """AES-GCM: 优先 cryptography,缺库时退回 CNG;认证失败报 DecryptError。"""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        return _decrypt_gcm_cng(key, nonce, blob)
    try:
        return AESGCM(key).decrypt(nonce, blob, None)
    except Exception as exc:
        raise DecryptError("AES-GCM 认证失败(%s)" % type(exc).__name__) from None
