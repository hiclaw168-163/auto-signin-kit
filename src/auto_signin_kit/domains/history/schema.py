"""history 域配置: 目前只有查询的默认回看天数。"""

from __future__ import annotations

from auto_signin_kit.core.config.schema import DomainConfig


class HistoryDomainConfig(DomainConfig):
    """``default_days``: ``history`` 不带参数时回看多少天(台账查询,非签到)。"""

    default_days: int = 7
