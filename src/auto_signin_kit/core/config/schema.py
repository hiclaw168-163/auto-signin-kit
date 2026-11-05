"""域配置 schema 基类: 各域在自己的 schema.py 里继承本类定义字段。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

#: 账户名单默认户: 不声明 accounts 的配置等价于单账户 default(向后兼容,
#: 存量凭据路径与台账 account 列都不动)。全库保留名,P2 凭据 profile 化
#: 按"账户名 → 凭据路径"解析时 default 走现状路径。
DEFAULT_ACCOUNT = "default"


class AccountRef(BaseModel):
    """配置里声明的单个账户(SPEC-12): 名字 + 启停 + 凭据位置。

    凭据位置四个字段全部可选,**语义由各域解释**(P2 凭据 profile 化):
    具名账户给了对应字段就用显式路径并忽略该域的全局覆盖;`default`
    账户(或字段缺省)沿用各域现状的探测/环境变量链路,存量行为不变。
    """

    model_config = {"extra": "forbid"}

    #: 账户名;台账 ``ask_accounts.UNIQUE(domain, name)`` 与它对齐。
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    #: 停用的账户静默调度直接跳过(不执行、不落库);显式 --account 手动跑放行。
    enabled: bool = True
    #: workbuddy: 该账户的凭据文件路径(桌面端 workbuddy-desktop.info 的副本)。
    auth_file: str | None = None
    #: qoder / trae: 该账户的客户端数据目录(com.qodercn.app* / Trae CN 的根)。
    auth_dir: str | None = None
    #: doubao: 该账户的 Chromium 数据目录(User Data 根,多开客户端各一份)。
    user_data_dir: str | None = None
    #: doubao: 该账户客户端的 CDP 调试端口(多开时各一个 --remote-debugging-port)。
    cdp_port: int | None = Field(default=None, ge=1, le=65535)


class DomainConfig(BaseModel):
    """所有域配置的基类。

    ``extra="forbid"``: 配置文件里出现 schema 之外的字段直接报错,
    而不是静默忽略——呼应"配置或草稿非法就显式报错,不做静默回退"的原则。
    """

    model_config = {"extra": "forbid"}

    #: 多账户声明(SPEC-12 P1);空列表 = 单账户 default。
    #: 放基类而非各域重复声明: 各域共用同一套校验与透传语义。
    accounts: list[AccountRef] = Field(default_factory=list)

    @field_validator("accounts")
    @classmethod
    def _accounts_unique(cls, value: list[AccountRef]) -> list[AccountRef]:
        """账户名重复直接报错——两户同名会让台账与凭据解析都失去意义。"""
        names = [ref.name for ref in value]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError("账户名重复: %s" % ", ".join(dupes))
        return value


class BudgetedConfig(DomainConfig):
    """带时间预算字段的域配置(计划任务 PT5M 上限口径: 240 秒)。

    抽取自 trae/qoder/doubao 三域逐字相同的 ``budget_seconds`` 定义
    (2026-11-03 四域共性分析结论 #4)。预算口径不同的域(如 workbuddy
    上限 540)不要继承本类,自己声明字段。
    """

    #: 时间预算(秒);环境变量优先读取由 core.kit.budget.parse_budget 承担。
    budget_seconds: float | None = Field(default=240.0, gt=0, le=240)
