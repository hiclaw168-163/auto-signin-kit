"""doubao 域配置,对应 ``config/domains/doubao.yaml``。"""

from __future__ import annotations

from auto_signin_kit.core.config.schema import BudgetedConfig

#: 豆包 Web 端 AGW 网关地址(DOUBAO_BASE_URL 环境变量优先)。
DEFAULT_BASE_URL = "https://www.doubao.com"
#: 签到活动标识(2026-10-30 上线的"签到领额度"活动,覆盖 8800 额度/30 天)。
DEFAULT_CAMPAIGN_CODE = "pc_incentive_task_mvp_v3"


class DoubaoDomainConfig(BudgetedConfig):
    """doubao 域配置项;``extra="forbid"``,未知字段直接报错。

    ``budget_seconds`` 继承自 BudgetedConfig(240 秒上限口径)。
    """

    base_url: str = DEFAULT_BASE_URL
    #: 活动标识;字节换季时会换 scene 值,届时改这里即可(见 docs/notes/NOTE-04)。
    campaign_code: str = DEFAULT_CAMPAIGN_CODE
    #: 豆包客户端 Chromium 数据目录;留空 = 自动探测(%LOCALAPPDATA%/Doubao/User Data)。
    user_data_dir: str | None = None
