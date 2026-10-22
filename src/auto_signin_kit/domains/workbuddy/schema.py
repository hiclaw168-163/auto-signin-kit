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
    #: 成长任务进度制造器(见 taskbot.py): 自动补齐「需客户端交互」任务的判据事件。
    #: 默认关——专家/技能类动作会消耗真实对话额度;开启后进度异步计分,领奖仍由
    #: 下一轮轮询的 run_tasks 收割。
    taskbot: bool = False
