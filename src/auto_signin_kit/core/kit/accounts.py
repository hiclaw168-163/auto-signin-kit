"""账户名解析与启停判定(SPEC-12 P1): 配置声明 → 运行期账户的统一入口。

账户注册制是**显式声明**(YAML ``accounts`` 列表),不是目录扫描发现制
(借鉴 workbuddy2api-panel 时的取舍,见 SPEC-12 §2.2): 名字拼错/未声明
在这里显式报错,绝不静默兜底到别的账户——签到落错账户的台账比缺一行
台账糟糕得多。
"""

from __future__ import annotations

from auto_signin_kit.core.config.schema import DEFAULT_ACCOUNT, AccountRef, DomainConfig
from auto_signin_kit.core.errors import ConfigError

__all__ = [
    "DEFAULT_ACCOUNT",
    "AccountRef",
    "account_enabled",
    "account_ref",
    "declared_accounts",
    "resolve_account",
]


def declared_accounts(config: DomainConfig) -> tuple[str, ...]:
    """配置里声明的账户名;未声明(空列表)回落单账户 default。"""
    names = tuple(ref.name for ref in (getattr(config, "accounts", None) or []))
    return names or (DEFAULT_ACCOUNT,)


def resolve_account(config: DomainConfig, account: str) -> str:
    """校验 ``account`` 在声明名单内并原样返回;未声明抛 ConfigError(退出码 2)。"""
    if account in declared_accounts(config):
        return account
    raise ConfigError(
        "账户 %r 未在配置中声明(可用: %s)" % (account, " / ".join(declared_accounts(config))))


def account_enabled(config: DomainConfig, account: str) -> bool:
    """账户启停;未声明的账户(含 default 空配置)视为启用。"""
    for ref in getattr(config, "accounts", None) or []:
        if ref.name == account:
            return ref.enabled
    return True


def account_ref(config: DomainConfig, account: str) -> AccountRef | None:
    """取账户声明(凭据位置字段都在上面);未声明的账户(空配置 default)返回 None。"""
    for ref in getattr(config, "accounts", None) or []:
        if ref.name == account:
            return ref
    return None
