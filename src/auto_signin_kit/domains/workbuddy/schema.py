"""workbuddy 域配置,对应 ``config/domains/workbuddy.yaml``。"""

from __future__ import annotations

from pydantic import Field

from auto_signin_kit.core.config.schema import DomainConfig

#: 官方客户端使用的 API 端点(签到位,一般不用改)。
DEFAULT_ENDPOINT = "https://copilot.tencent.com"


class WorkbuddyDomainConfig(DomainConfig):
    """workbuddy 域配置项。

    ``extra="forbid"``: 配置里混进 schema 之外的字段直接报错,不做静默忽略。
    endpoint 兜底取凭证里的 ``auth.endpoint``,都没有才用默认端点。
    """

    endpoint: str = DEFAULT_ENDPOINT
    #: 网络请求时间预算(秒);环境变量 WORKBUDDY_BUDGET_SECONDS 优先,详见 service 层。
    budget_seconds: float | None = Field(default=None, gt=0, le=540)
    #: 成长中心轮询时空跑(已签 + 无可领取项)默认不写日志;设 True 则逐轮落盘。
    growth_log_empty: bool = False
