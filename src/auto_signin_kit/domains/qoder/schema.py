"""qoder 域配置,对应 ``config/domains/qoder.yaml``。"""

from __future__ import annotations

from pydantic import Field

from auto_signin_kit.core.config.schema import DomainConfig

#: Qoder CN 开放 API 端点(QODER_BASE_URL 环境变量优先)。
DEFAULT_BASE_URL = "https://openapi.qoder.com.cn"


class QoderDomainConfig(DomainConfig):
    """qoder 域配置项;``extra="forbid"``,未知字段直接报错。"""

    base_url: str = DEFAULT_BASE_URL
    #: 空跑(已领取/没有活动)默认不写日志;True 则逐轮落盘。
    log_empty: bool = Field(default=False)
    #: 时间预算(秒);计划任务 PT5M 对应上限 240,QODER_BUDGET_SECONDS 优先。
    budget_seconds: float | None = Field(default=240.0, gt=0, le=240)
