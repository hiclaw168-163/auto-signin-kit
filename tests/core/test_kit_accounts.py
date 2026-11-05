"""core.kit.accounts 测试: 账户声明的解析/校验/启停(SPEC-12 P1)。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from auto_signin_kit.core.config.schema import DEFAULT_ACCOUNT, AccountRef, DomainConfig
from auto_signin_kit.core.errors import ConfigError
from auto_signin_kit.core.kit.accounts import (
    account_enabled,
    account_ref,
    declared_accounts,
    resolve_account,
)


def test_empty_accounts_falls_back_to_default():
    cfg = DomainConfig()
    assert declared_accounts(cfg) == (DEFAULT_ACCOUNT,)
    assert resolve_account(cfg, "default") == "default"


def test_declared_accounts_resolve():
    cfg = DomainConfig(accounts=[AccountRef(name="main"), AccountRef(name="backup")])
    assert declared_accounts(cfg) == ("main", "backup")
    assert resolve_account(cfg, "backup") == "backup"


def test_undeclared_account_raises_config_error():
    """显式声明制: --account 拼错/未声明直接报错,绝不静默兜底到别的账户。"""
    cfg = DomainConfig(accounts=[AccountRef(name="main")])
    with pytest.raises(ConfigError) as exc:
        resolve_account(cfg, "ghost")
    assert "ghost" in str(exc.value) and "main" in str(exc.value)


def test_duplicate_account_names_rejected():
    with pytest.raises(ValidationError):
        DomainConfig(accounts=[AccountRef(name="a"), AccountRef(name="a")])


def test_invalid_account_name_rejected():
    with pytest.raises(ValidationError):
        AccountRef(name="Bad Name")  # 大写与空格都不在名字词表内


def test_account_enabled_flags():
    cfg = DomainConfig(accounts=[AccountRef(name="main", enabled=False),
                                 AccountRef(name="backup")])
    assert account_enabled(cfg, "main") is False
    assert account_enabled(cfg, "backup") is True
    assert account_enabled(DomainConfig(), "default") is True  # 未声明视同启用


# --------------------------------------------------------------- SPEC-12 P2 凭据 profile

def test_account_ref_returns_declaration():
    ref = AccountRef(name="main", auth_dir="D:/QoderWork")
    cfg = DomainConfig(accounts=[ref])
    assert account_ref(cfg, "main") is ref
    assert account_ref(cfg, "ghost") is None
    assert account_ref(DomainConfig(), "default") is None  # 空配置无声明可取
