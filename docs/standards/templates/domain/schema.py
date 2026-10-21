"""workbuddy 域配置。"""

from __future__ import annotations

from auto_signin_kit.core.config.schema import DomainConfig


class WorkbuddyDomainConfig(DomainConfig):
    """workbuddy 域的配置项,对应 ``config/domains/workbuddy.yaml``。

    先只放真的需要的字段;不要为了"看起来完整"预先加一堆用不上的配置项。
    """

    # 示例字段,按需替换/删除:
    # margin_mm: float = Field(default=15, ge=5, le=40)
