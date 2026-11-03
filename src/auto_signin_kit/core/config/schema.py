"""域配置 schema 基类: 各域在自己的 schema.py 里继承本类定义字段。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DomainConfig(BaseModel):
    """所有域配置的基类。

    ``extra="forbid"``: 配置文件里出现 schema 之外的字段直接报错,
    而不是静默忽略——呼应"配置或草稿非法就显式报错,不做静默回退"的原则。
    """

    model_config = {"extra": "forbid"}


class BudgetedConfig(DomainConfig):
    """带时间预算字段的域配置(计划任务 PT5M 上限口径: 240 秒)。

    抽取自 trae/qoder/doubao 三域逐字相同的 ``budget_seconds`` 定义
    (2026-11-03 四域共性分析结论 #4)。预算口径不同的域(如 workbuddy
    上限 540)不要继承本类,自己声明字段。
    """

    #: 时间预算(秒);环境变量优先读取由 core.kit.budget.parse_budget 承担。
    budget_seconds: float | None = Field(default=240.0, gt=0, le=240)
