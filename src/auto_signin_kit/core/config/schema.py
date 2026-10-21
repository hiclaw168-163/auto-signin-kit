"""域配置 schema 基类: 各域在自己的 schema.py 里继承本类定义字段。"""

from __future__ import annotations

from pydantic import BaseModel


class DomainConfig(BaseModel):
    """所有域配置的基类。

    ``extra="forbid"``: 配置文件里出现 schema 之外的字段直接报错,
    而不是静默忽略——呼应"配置或草稿非法就显式报错,不做静默回退"的原则。
    """

    model_config = {"extra": "forbid"}
