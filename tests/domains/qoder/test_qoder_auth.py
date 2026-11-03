"""qoder auth 单元测试: ISO 时间解析 / 令牌过期边界 / 数据目录发现(全离线)。"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from auto_signin_kit.core.adapters.platform_info import IS_WINDOWS
from auto_signin_kit.core.kit.tokens import parse_iso as _parse_iso
from auto_signin_kit.domains.qoder import auth
from auto_signin_kit.domains.qoder.auth import (
    AuthFormatError,
    NoAuthError,
    QoderAuthError,
    resolve_token,
    token_expired,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """令牌与目录发现全部指向临时环境,绝不碰本机真实登录态。"""
    monkeypatch.delenv("QODER_TOKEN", raising=False)
    monkeypatch.setenv("QODER_AUTH_DIR", str(tmp_path / "empty"))


class TestParseIso:
    def test_z_suffix_and_naive_treated_as_utc(self):
        """Z 后缀与无时区串都按 UTC 解出带时区时间;显式偏移被保留。"""
        assert _parse_iso("2025-01-01T00:00:00Z").tzinfo is not None
        assert _parse_iso("2025-01-01T00:00:00").utcoffset() == timedelta(0)
        assert _parse_iso("2025-01-01T00:00:00+08:00").utcoffset() == timedelta(hours=8)

    def test_invalid_and_missing_values_return_none(self):
        """空值/非时间串/非字符串标量都给 None,不抛异常。"""
        for bad in (None, "", "   ", "not-a-time", 123, [], {}):
            assert _parse_iso(bad) is None

    def test_surrounding_whitespace_tolerated(self):
        """首尾空白被容忍。"""
        assert _parse_iso("  2025-01-01T00:00:00Z  ") is not None


class TestTokenExpired:
    def test_past_expiry_is_expired(self):
        """过期时间在过去 → True。"""
        assert token_expired({"expiresAt": "2001-01-01T00:00:00Z"}) is True

    def test_future_expiry_is_valid(self):
        """过期时间在未来 → False。"""
        assert token_expired({"expiresAt": "2099-01-01T00:00:00Z"}) is False

    def test_boundary_one_second_each_side(self):
        """以当前时刻为界的秒级边界: 前两秒已过期,后两秒未过期。"""
        now = datetime.now(UTC)
        past = (now - timedelta(seconds=2)).isoformat()
        future = (now + timedelta(seconds=2)).isoformat()
        assert token_expired({"expiresAt": past}) is True
        assert token_expired({"expiresAt": future}) is False

    def test_missing_or_invalid_expiry_fails_open(self):
        """expiresAt 缺失/非法按未过期处理(由服务端 401 兜底,不本地猜)。"""
        assert token_expired({}) is False
        assert token_expired({"expiresAt": "garbage"}) is False


class TestFindAuthDirs:
    def test_override_env_wins_without_existence_filter(self, monkeypatch, tmp_path):
        """QODER_AUTH_DIR 覆盖一切,目录不存在也原样返回(供"目录在但未登录"分支)。"""
        target = tmp_path / "com.qodercn.app0"
        monkeypatch.setenv("QODER_AUTH_DIR", str(target))
        assert auth.find_auth_dirs() == [target]

    def _force_windows(self, monkeypatch, root):
        """把平台分支钉到 Windows 形状(本测试文件要在任何平台都可跑)。"""
        monkeypatch.setattr(auth, "IS_MACOS", False)
        monkeypatch.setattr(auth, "_default_roots", lambda: [root])

    def test_sorted_by_mtime_descending(self, monkeypatch, tmp_path):
        """多个目录按修改时间新→旧排序,客户端最近写的优先。"""
        monkeypatch.delenv("QODER_AUTH_DIR")
        self._force_windows(monkeypatch, tmp_path)
        old = tmp_path / "com.qodercn.appA"
        new = tmp_path / "com.qodercn.appB"
        old.mkdir()
        new.mkdir()
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        assert auth.find_auth_dirs() == [new, old]

    def test_missing_appdata_returns_empty(self, monkeypatch, tmp_path):
        """根指向不存在路径时空列表,不抛异常。"""
        monkeypatch.delenv("QODER_AUTH_DIR")
        self._force_windows(monkeypatch, tmp_path / "nope")
        assert auth.find_auth_dirs() == []

    def test_mac_scans_both_bundle_ids(self, monkeypatch, tmp_path):
        """mac 双模式: 国际版 com.qoder.app* 与 CN 版 com.qodercn.app* 都命中。"""
        monkeypatch.delenv("QODER_AUTH_DIR")
        monkeypatch.setattr(auth, "IS_MACOS", True)
        monkeypatch.setattr(auth, "_default_roots", lambda: [tmp_path])
        intl = tmp_path / "com.qoder.app.stable"
        cn = tmp_path / "com.qodercn.app0"
        unrelated = tmp_path / "com.other.app"
        for d in (intl, cn, unrelated):
            d.mkdir()
        os.utime(intl, (1000, 1000))
        os.utime(cn, (2000, 2000))
        os.utime(unrelated, (3000, 3000))
        assert auth.find_auth_dirs() == [cn, intl]


class TestMacDecrypt:
    """mac 解锁链(纯 cryptography,任何平台可跑): Keychain 密码→PBKDF2→CBC。"""

    def test_roundtrip_recovers_session_json(self):
        from auto_signin_kit.core.adapters import mac_keychain

        blob = mac_keychain.encrypt_chrome_mac_v10(b'{"token":"t1"}', "pw-123")
        assert mac_keychain.decrypt_chrome_mac_v10(blob, "pw-123") == b'{"token":"t1"}'

    def test_bad_prefix_and_wrong_password_raise(self):
        from auto_signin_kit.core.adapters import mac_keychain
        from auto_signin_kit.core.errors import AdapterError

        blob = mac_keychain.encrypt_chrome_mac_v10(b'{"token":"t1"}', "right")
        with pytest.raises(AdapterError, match="v10"):
            mac_keychain.decrypt_chrome_mac_v10(b"v99" + blob[3:], "right")
        with pytest.raises(AdapterError, match="填充"):
            mac_keychain.decrypt_chrome_mac_v10(blob, "wrong")

    def test_load_session_mac_full_chain(self, monkeypatch, tmp_path):
        """mac 全链路(离线): 目录发现→读凭据→Keychain 取密码→解出 token。"""
        from auto_signin_kit.core.adapters import mac_keychain

        monkeypatch.delenv("QODER_AUTH_DIR")
        monkeypatch.setattr(auth, "IS_MACOS", True)
        monkeypatch.setattr(auth, "_default_roots", lambda: [tmp_path])
        root = tmp_path / "com.qoder.app.stable"
        root.mkdir()
        (root / auth.AUTH_FILE).write_bytes(
            mac_keychain.encrypt_chrome_mac_v10(
                b'{"token":" mac-tok ","expiresAt":"2099-01-01T00:00:00Z"}', "kw"))
        monkeypatch.setattr(mac_keychain, "read_password", lambda s, a: "kw")
        sess = auth.load_session()
        assert sess["token"] == " mac-tok "
        assert sess["_auth_dir"] == str(root)

    def test_load_session_mac_without_keychain_reports_format_error(
            self, monkeypatch, tmp_path):
        """Keychain 条目缺失要报 AuthFormatError(登录态在但读不出),不是静默。"""
        from auto_signin_kit.core.adapters import mac_keychain

        monkeypatch.delenv("QODER_AUTH_DIR")
        monkeypatch.setattr(auth, "IS_MACOS", True)
        monkeypatch.setattr(auth, "_default_roots", lambda: [tmp_path])
        root = tmp_path / "com.qoder.app.stable"
        root.mkdir()
        (root / auth.AUTH_FILE).write_bytes(b"v10" + bytes(64))
        monkeypatch.setattr(
            mac_keychain, "read_password",
            lambda s, a: (_ for _ in ()).throw(
                mac_keychain.KeychainError("条目不存在")))
        with pytest.raises(AuthFormatError):
            auth.load_session()


class TestResolveToken:
    def test_env_token_wins_and_strips(self, monkeypatch):
        """环境变量令牌优先,且去首尾空白;不触发本地解锁。"""
        monkeypatch.setenv("QODER_TOKEN", "  tok-1  ")
        assert resolve_token() == ("tok-1", None)

    @pytest.mark.skipif(IS_WINDOWS, reason="Windows 上会走本地 DPAPI 解锁路径")
    def test_blank_env_falls_through_and_fails_closed(self, monkeypatch):
        """纯空白环境变量不算令牌;非 Windows 直接显式报 NoAuth,不静默兜底。"""
        monkeypatch.setenv("QODER_TOKEN", "   ")
        with pytest.raises(NoAuthError):
            resolve_token()


def test_auth_error_carries_looked_in_snapshot():
    """QoderAuthError 复制 looked_in 列表(后续修改不影响),None 归一为空列表。"""
    source = [Path("/a")]
    err = QoderAuthError("msg", source)
    source.append(Path("/b"))
    assert err.looked_in == [Path("/a")]
    assert NoAuthError("x").looked_in == []


class TestDetectApiBase:
    def test_intl_session_prefers_openapi_domain_in_context(self):
        """国际版实测 context: 直取其中的 openapi.* 域。"""
        sess = {"authenticationContext": [
            "https://qoder.com", "https://openapi.qoder.sh",
            "732aef47-9cf2-46a2-95fe-4cebb5d0d1fa", "prod"]}
        assert auth.detect_api_base(sess) == "https://openapi.qoder.sh"

    def test_context_stored_as_json_string_is_unpacked(self):
        """真机会话里 context 存的是 JSON 字符串(2026-11-02 实测),先反序列化。"""
        import json as _json
        sess = {"authenticationContext": _json.dumps([
            "https://qoder.com", "https://openapi.qoder.sh", "prod"])}
        assert auth.detect_api_base(sess) == "https://openapi.qoder.sh"

    def test_cn_session_maps_issuer_domain(self):
        """CN 版 context 按主域映射到 openapi.qoder.com.cn。"""
        sess = {"authenticationContext": [
            "https://qoder.com.cn", "01a19c7a-337b-79f5-9b74-f014cef4ab6a", "prod"]}
        assert auth.detect_api_base(sess) == "https://openapi.qoder.com.cn"

    def test_unknown_or_malformed_context_returns_none(self):
        """context 缺失/非列表/无 http 域时给 None,由调用方回落配置。"""
        assert auth.detect_api_base(None) is None
        assert auth.detect_api_base({}) is None
        assert auth.detect_api_base({"authenticationContext": "qoder.com"}) is None
        assert auth.detect_api_base({"authenticationContext": ["prod", "732a"]}) is None

    def test_trailing_slash_stripped_and_bare_host_skipped(self):
        """域尾斜杠被去掉;无协议头的裸域跳过,主域映射兜底生效。"""
        sess = {"authenticationContext": ["openapi.qoder.sh", "https://qoder.com/"]}
        assert auth.detect_api_base(sess) == "https://openapi.qoder.sh"
