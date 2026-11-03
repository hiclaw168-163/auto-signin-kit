"""core.kit.tokens 令牌过期判定测试(K1 抽取自 trae/qoder 逐字重复)。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from auto_signin_kit.core.kit.tokens import parse_iso, token_expired


def test_parse_iso_handles_naive_as_utc():
    moment = parse_iso("2026-11-03T10:00:00")
    assert moment is not None and moment.tzinfo == UTC


def test_parse_iso_handles_z_suffix_and_tz():
    assert parse_iso("2026-11-03T10:00:00Z") is not None
    assert parse_iso("2026-11-03T10:00:00+08:00") is not None


def test_parse_iso_rejects_empty_and_garbage():
    assert parse_iso("") is None
    assert parse_iso(None) is None
    assert parse_iso("not-a-date") is None
    assert parse_iso(12345) is None


def test_token_expired_two_states():
    future = {"expiredAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
    past = {"expiredAt": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
    assert token_expired(future) is False
    assert token_expired(past) is True


def test_token_expired_missing_field_means_not_expired():
    assert token_expired({}) is False


def test_token_expired_custom_field():
    past = {"expiresAt": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
    assert token_expired(past, field="expiresAt") is True
