"""trae 域配置,对应 ``config/domains/trae.yaml``。"""

from __future__ import annotations

from auto_signin_kit.core.config.schema import BudgetedConfig

#: Trae CN 签到/积分接口宿主(TRAE_BASE_URL 环境变量优先)。
DEFAULT_BASE_URL = "https://api.trae.cn"


class TraeDomainConfig(BudgetedConfig):
    """trae 域配置项;``extra="forbid"``,未知字段直接报错。

    ``budget_seconds`` 继承自 BudgetedConfig(240 秒上限口径)。
    """

    #: 接口宿主;本地登录态的 location 覆盖见 auth 模块(TRAE_AUTH_DIR)。
    base_url: str = DEFAULT_BASE_URL
    #: 空跑(已领取等)默认不写日志;True 则逐轮落盘。
    log_empty: bool = False
    #: 桌面端数据目录(含 storage.json 的根);None=按平台默认探测。
    data_dir: str | None = None
