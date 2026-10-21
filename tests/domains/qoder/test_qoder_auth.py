"""qoder auth 单元测试: ISO 时间解析 / 令牌过期边界 / 数据目录发现(全离线)。"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from auto_signin_kit.core.adapters.platform_info import IS_WINDOWS
from auto_signin_kit.domains.qoder import auth
from auto_signin_kit.domains.qoder.auth import (
    NoAuthError,
    QoderAuthError,
    _parse_iso,
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

    def test_sorted_by_mtime_descending(self, monkeypatch, tmp_path):
        """多个目录按修改时间新→旧排序,客户端最近写的优先。"""
        monkeypatch.delenv("QODER_AUTH_DIR")
        monkeypatch.setenv("APPDATA", str(tmp_path))
        old = tmp_path / "com.qodercn.appA"
        new = tmp_path / "com.qodercn.appB"
        old.mkdir()
        new.mkdir()
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        assert auth.find_auth_dirs() == [new, old]

    def test_missing_appdata_returns_empty(self, monkeypatch, tmp_path):
        """APPDATA 指向不存在路径时空列表,不抛异常。"""
        monkeypatch.delenv("QODER_AUTH_DIR")
        monkeypatch.setenv("APPDATA", str(tmp_path / "nope"))
        assert auth.find_auth_dirs() == []


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
