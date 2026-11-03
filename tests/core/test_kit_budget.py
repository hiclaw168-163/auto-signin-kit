"""core.kit.budget 预算解析测试(K2 抽取自三域近似重复)。"""

from __future__ import annotations

import pytest

from auto_signin_kit.core.kit.budget import parse_budget


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("TEST_BUDGET_SECONDS", raising=False)


def test_env_absent_uses_configured():
    assert parse_budget(90.0, 120.0, 240.0, "TEST_BUDGET_SECONDS") == (90.0, None)


def test_env_absent_config_missing_falls_to_default():
    assert parse_budget(None, 120.0, 240.0, "TEST_BUDGET_SECONDS") == (120.0, None)


def test_env_overrides_config(monkeypatch):
    monkeypatch.setenv("TEST_BUDGET_SECONDS", "30")
    assert parse_budget(90.0, 120.0, 240.0, "TEST_BUDGET_SECONDS") == (30.0, None)


def test_env_non_numeric_falls_to_default_with_warning(monkeypatch):
    monkeypatch.setenv("TEST_BUDGET_SECONDS", "abc")
    value, warning = parse_budget(90.0, 120.0, 240.0, "TEST_BUDGET_SECONDS")
    assert value == 120.0
    assert warning is not None and "不是数字" in warning


def test_env_non_positive_falls_to_default_with_warning(monkeypatch):
    for raw in ("0", "-5"):
        monkeypatch.setenv("TEST_BUDGET_SECONDS", raw)
        value, warning = parse_budget(90.0, 120.0, 240.0, "TEST_BUDGET_SECONDS")
        assert value == 120.0
        assert warning is not None and "必须为正数" in warning


def test_over_maximum_clamped_with_warning(monkeypatch):
    monkeypatch.setenv("TEST_BUDGET_SECONDS", "300")
    value, warning = parse_budget(90.0, 120.0, 240.0, "TEST_BUDGET_SECONDS")
    assert value == 240.0
    assert warning is not None and "240" in warning


def test_never_raises_on_env_input():
    # env 是唯一不可信输入,任何 env 值都不抛异常(configured 由 pydantic 保证)
    import os

    for raw in ("", "abc", "0", "-5", "1e999", "nan"):
        os.environ["TEST_BUDGET_SECONDS"] = raw
        parse_budget(90.0, 120.0, 240.0, "TEST_BUDGET_SECONDS")
