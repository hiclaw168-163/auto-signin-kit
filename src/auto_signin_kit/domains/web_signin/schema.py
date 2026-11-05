"""web_signin 声明式应用 schema: ``config/apps/<app_code>.yaml`` 的校验模型。

设计立场: 新应用接入 = 写一份 YAML,零 Python 代码、零 entry_points 改动。
引擎只解释「status 查询 → 幂等 claim → 确认」同构形态的签到;不合形态的
(多步编排/活动分类)继续走代码域,不为抽象而抽象(STD-07)。

``extra="forbid"``: YAML 里出现 schema 之外的字段直接报错(AGENTS 规则 4),
字段拼错在加载期就精确指出来,不是运行到半路才发现。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from auto_signin_kit.core.config.schema import AccountRef

#: ``business_codes`` 允许映射到的结果标签(超出集合的映射没有意义,拒绝加载)。
BUSINESS_RESULTS = ("AUTH_REJECTED", "FORBIDDEN", "ERROR")

APP_CODE_PATTERN = r"^[a-z][a-z0-9_]{1,31}$"


class TokenHeader(BaseModel):
    """令牌请求头: ``{name}: {prefix}{token}``;不声明则令牌只入脱敏表不发头。"""

    model_config = {"extra": "forbid"}

    name: str = "Authorization"
    prefix: str = ""


class AuthDecl(BaseModel):
    """登录态来源;``kind`` 决定由哪个适配器解释(注册表见 authkit)。

    - ``plain``: 普通 JSON 令牌文件——``files`` 为候选文件 glob(~ 展开),
      ``token_json_path`` 从文件 JSON 里点路径取令牌(**plain 必填**);
    - ``ss_aes``: Trae 桌面端 SS_AES 加密信封——``files`` 为客户端数据目录
      (缺省按平台默认探测 Trae CN/Trae),信封解密与设备头由适配器承担。
    """

    model_config = {"extra": "forbid"}

    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    #: 凭据候选: plain=文件 glob;ss_aes=数据目录;空列表 = 适配器平台默认。
    files: list[str] = Field(default_factory=list)
    #: 环境变量令牌(可选): 仅 default 账户消费——具名账户用显式路径,
    #: 混用会拿 A 户令牌签 B 户的到(沿用 trae 域的隔离口径)。
    env: str | None = None
    #: plain: 令牌在文件 JSON 里的点路径。
    token_json_path: str | None = None
    #: plain: 有效期字段在文件 JSON 里的点路径(可选)。
    expires_json_path: str | None = None
    #: 令牌请求头(可选)。
    token_header: TokenHeader | None = None
    #: 适配器产出的额外头之上的静态追加(如 X-User-Region)。
    headers: dict[str, str] = Field(default_factory=dict)


class RequestDecl(BaseModel):
    """一个 HTTP 请求动作的声明(method/path/body/动作级静态头)。"""

    model_config = {"extra": "forbid"}

    method: str = Field(default="POST", pattern=r"^(GET|POST)$")
    path: str = Field(pattern=r"^/")
    body: dict = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)


class StatusDecl(RequestDecl):
    """状态查询声明;``mapping`` 把响应 JSON 点路径映射到统一字段。"""

    #: 统一字段 → 响应点路径;键支持 checked_in / credits / extra_credits。
    mapping: dict[str, str] = Field(default_factory=dict)

    @field_validator("mapping")
    @classmethod
    def _mapping_keys(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(value) - {"checked_in", "credits", "extra_credits"})
        if unknown:
            raise ValueError("mapping 不支持的字段: %s(允许 checked_in/credits/extra_credits)"
                             % ", ".join(unknown))
        return value


class ClaimDecl(RequestDecl):
    """领取声明;成功判定/限流重试/领后复查是引擎流程的旋钮。"""

    #: 领取响应里直接回显"已签"字段时的判定点路径(可选)。
    checked_in_path: str | None = None
    #: 领取数额在响应里的点路径(可选;缺省回落 status.mapping.credits)。
    amount_path: str | None = None
    #: 命中即视为限流的业务码 → 同轮退避重试(30+60+120s,预算内)。
    retry_on: list[int] = Field(default_factory=list)
    #: 领取受理成功但响应不回显结果时,以 status 复查确认(trae 模式)。
    confirm_by_status: bool = False


class ScheduleDecl(BaseModel):
    """调度默认值;可被 ``config/domains/schedule.yaml`` 的 tasks 覆盖。"""

    model_config = {"extra": "forbid"}

    times: list[str] = Field(default_factory=lambda: ["09:05"])
    limit_minutes: int = Field(default=5, ge=1, le=60)

    @field_validator("times")
    @classmethod
    def _times_shape(cls, value: list[str]) -> list[str]:
        bad = [t for t in value if len(t) != 5 or t[2] != ":"
               or not (t[:2].isdigit() and t[3:].isdigit())]
        if bad:
            raise ValueError("times 必须是 HH:MM 形状: %s" % ", ".join(bad))
        return value


class AppDeclaration(BaseModel):
    """一份声明式应用的完整定义(``config/apps/<app_code>.yaml``)。

    ``app_code`` 同时是台账 ``domain`` 列与 ``ask_apps.app_code`` 的取值,
    三处天然对齐;引擎落台账时直接用它,不再有"域名 ≠ 应用编码"的转换。
    """

    model_config = {"extra": "forbid"}

    app_code: str = Field(pattern=APP_CODE_PATTERN)
    region: str = Field(default="cn", pattern=r"^(cn|intl)$")
    display: str = Field(min_length=1)
    base_url: str = Field(pattern=r"^https?://")
    ready_noun: str = "签到"
    #: 单请求超时(秒)。
    timeout: float = Field(default=20.0, ge=1.0, le=60.0)
    #: 本次运行的网络时间预算(秒);计划任务 PT5M 口径上限 240。
    budget_seconds: float = Field(default=240.0, gt=0, le=240)
    auth: AuthDecl
    #: 全局静态头(所有请求共用;优先级低于凭证头)。
    headers: dict[str, str] = Field(default_factory=dict)
    #: 响应业务码 → 结果标签(如 1001: AUTH_REJECTED);未命中按 ERROR 处理。
    business_codes: dict[int, str] = Field(default_factory=dict)
    #: 业务码字段点路径(status/claim 共用;服务端 HTTP 200 + 业务码协议才有意义)。
    code_path: str = "code"
    #: 业务码成功值。
    code_ok: int = 0
    status: StatusDecl | None = None
    claim: ClaimDecl
    schedule: ScheduleDecl = Field(default_factory=ScheduleDecl)
    #: 多账户声明(SPEC-12): 复用 core 的 AccountRef,凭据位置字段语义由
    #: 适配器解释(plain 用 auth_file,ss_aes 用 auth_dir)。
    accounts: list[AccountRef] = Field(default_factory=list)

    @field_validator("business_codes")
    @classmethod
    def _business_labels(cls, value: dict[int, str]) -> dict[int, str]:
        bad = {label for label in value.values() if label not in BUSINESS_RESULTS}
        if bad:
            raise ValueError("business_codes 只允许映射到 %s,收到 %s"
                             % ("/".join(BUSINESS_RESULTS), ", ".join(sorted(bad))))
        return value

    @field_validator("auth")
    @classmethod
    def _auth_kind_needs(cls, value: AuthDecl) -> AuthDecl:
        if value.kind == "plain" and not value.token_json_path:
            raise ValueError("auth.kind=plain 必须声明 token_json_path")
        return value
