"""mac_keychain 适配器: 标准 Electron v10 与魔改族(preamble)切片加解密回环。"""

from __future__ import annotations

import pytest

from auto_signin_kit.core.adapters.mac_keychain import (
    decrypt_chrome_mac_v10,
    encrypt_chrome_mac_v10,
)
from auto_signin_kit.core.errors import AdapterError

PW = "keychain-password"


def test_standard_layout_roundtrip():
    assert decrypt_chrome_mac_v10(encrypt_chrome_mac_v10(b"abc", PW), PW) == b"abc"


def test_preamble_layout_roundtrip_and_shape():
    """魔改形状: v10 + 16 字节会话头 + 16 字节 IV + 密文(整 16 对齐)。"""
    blob = encrypt_chrome_mac_v10(b"x" * 40, PW, preamble=16)
    assert blob[:3] == b"v10" and len(blob) == 3 + 16 + 16 + 48
    assert decrypt_chrome_mac_v10(blob, PW, preamble=16) == b"x" * 40


def test_wrong_slice_does_not_recover_value():
    """按标准切片(固定空格 IV)解魔改码流: 要么填充非法报错,要么解不出原值。"""
    value = b"9c18d6b0ef1eb14bace5f4d4a058c419"
    blob = encrypt_chrome_mac_v10(value, PW, preamble=16)
    try:
        plain = decrypt_chrome_mac_v10(blob, PW)
    except AdapterError:
        return
    assert plain != value


def test_prefix_must_be_v10():
    with pytest.raises(AdapterError):
        decrypt_chrome_mac_v10(b"v11" + b"\x00" * 32, PW, preamble=16)


def test_preamble_body_too_short_raises():
    with pytest.raises(AdapterError):
        decrypt_chrome_mac_v10(b"v10" + b"H" * 16 + b"\x00" * 4, PW, preamble=16)
