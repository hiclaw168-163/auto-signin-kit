"""qoder 域配置,对应 ``config/domains/qoder.yaml``。"""

from __future__ import annotations

from auto_signin_kit.core.config.schema import BudgetedConfig

#: Qoder CN 开放 API 端点(QODER_BASE_URL 环境变量优先)。
DEFAULT_BASE_URL = "https://openapi.qoder.com.cn"


class QoderDomainConfig(BudgetedConfig):
    """qoder 域配置项;``extra="forbid"``,未知字段直接报错。

    ``budget_seconds`` 继承自 BudgetedConfig(240 秒上限口径)。
    """

    base_url: str = DEFAULT_BASE_URL
    #: 空跑(已领取/没有活动)默认不写日志;True 则逐轮落盘。
    log_empty: bool = False
