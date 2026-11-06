"""API 契约模型(P0 契约层,PROPOSAL-01): OpenAPI 单一事实源。

每个端点的响应形状在本模块定义为 pydantic 模型,路由用
``response_model=`` 挂接——FastAPI 由此产出规范 OpenAPI 3.1 契约,
``openapi-typescript`` 按契约生成五端共用的 TS 类型(见
``frontend/packages/api-client``)。

约定:
- **形状对齐 core/dto.py 的行 DTO 与各 store 查询的真实输出**——契约
  谎报形状比没有契约更糟,改 store 输出必须同步改这里;
- 数字口径: 台账 SUM 可能出 float,积分类字段一律 ``float``;布尔登记态
  (enabled)在 store 层已是 bool,保持 bool;``needs_attention`` 是
  SQLite 的 0/1 整数,如实 ``int``(前端按真值用);
- 松散形状(YAML 字段视图 ``fields``/声明原文 ``config``)如实
  ``dict[str, Any]``——它们是"用户数据的透传视图",不是稳定契约;
- 校验从宽(默认忽略多余键): 服务端多给字段不炸旧客户端,契约演进
  只加不改,破坏性变更升接口路径版本。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class DayBlock(BaseModel):
    """14 天色块的单日项(day=日期,ok=当天是否签到成功)。"""

    day: str
    ok: bool


class DailyRow(BaseModel):
    """``ask_daily_checkins`` 视图行(每天每域每账户最终状态),对齐
    ``core.dto.DailyCheckinRow``。"""

    checkin_day: str
    domain: str
    account_name: str | None = None
    result: str
    source: str
    needs_attention: int = 0
    report: str | None = None
    executed_at: str


class EventRow(BaseModel):
    """运行明细行,对齐 ``core.dto.SigninRunRow`` 的对外子集
    (request/response 是入参与结果的 JSON 文本,原样透传)。"""

    executed_at: str
    domain: str
    account_name: str | None = None
    operation: str | None = None
    source: str
    result: str
    needs_attention: int = 0
    report: str | None = None
    request: str | None = None
    response: str | None = None


class OverviewCard(BaseModel):
    """看板域卡片(今日状态 + 14 天色块 + 连签/积分统计)。"""

    domain: str
    today: DailyRow | None = None
    today_signed: bool = False
    today_attention: bool = False
    week_ok: int = 0
    week_total: int = 0
    streak: int = 0
    days14: list[DayBlock] = []
    credits: float = 0
    today_credits: float = 0
    last_run: str | None = None


class OverviewResponse(BaseModel):
    """看板总览响应(域卡片集合)。"""

    cards: list[OverviewCard]
    generated_at: str


class AccountCard(BaseModel):
    """账户视图行(登记档案 × 运行统计合并)。"""

    domain: str
    name: str
    enabled: bool
    daily_potential: int | None = None
    active: bool = False
    last_earned_day: str | None = None
    credits_recent: float = 0
    client_version: str | None = None
    device_id: str | None = None
    install_path: str | None = None
    uid: str | None = None
    client_info: dict[str, Any] | None = None
    info_synced_at: str | None = None
    streak: int = 0
    days14: list[DayBlock] = []
    credits: float = 0
    today_credits: float = 0
    last_run: str | None = None


class AccountsResponse(BaseModel):
    """账户清单响应(总数/活跃数/窗口天数/行集合)。"""

    total: int
    active: int
    window_days: int
    accounts: list[AccountCard]


class AccountToggleResult(BaseModel):
    """账户启用/停用的写回结果。"""

    ok: bool
    domain: str
    name: str
    enabled: bool


class CreditTrendPoint(BaseModel):
    """积分趋势的单日点(day,所得 credits)。"""

    day: str
    credits: float


class CreditDomainRow(BaseModel):
    """分域积分汇总行(累计/窗口内/最近所得日)。"""

    domain: str
    total: float
    window_total: float
    last_day: str | None = None


class DomainBalance(BaseModel):
    """累计所得快照(口径警示见 store.credits_stats: 不是可用余额)。"""

    total_credits: float | None = None
    updated_at: str | None = None


class CreditsStatsResponse(BaseModel):
    """积分统计响应(趋势 + 分域 + 累计所得快照)。"""

    window_days: int
    trend: list[CreditTrendPoint] = []
    by_domain: list[CreditDomainRow] = []
    balances: dict[str, DomainBalance] = {}


class CreditDailyRow(BaseModel):
    """积分日明细行(checkin_day, domain, credits)。"""

    checkin_day: str
    domain: str
    credits: float


class CapacityRow(BaseModel):
    """单日产能行(实际所得 earned × 潜力 potential → 达成率 rate)。"""

    day: str
    earned: float
    potential: int | None = None
    rate: float | None = None


class CreditsSummaryResponse(BaseModel):
    """积分总览响应(日明细 + 账户汇总 + 产能)。"""

    daily: list[CreditDailyRow] = []
    accounts: AccountsResponse
    capacity: list[CapacityRow] = []


class ScheduleTask(BaseModel):
    """调度任务状态行(注册表声明 × schtasks/launchd 实况)。"""

    name: str
    installed: bool
    times: list[str] = []
    detail: str | None = None


class ScheduleStatusResponse(BaseModel):
    """调度状态响应(平台 + 任务清单)。"""

    platform: str
    tasks: list[ScheduleTask] = []


class ScheduleOpItem(BaseModel):
    """install/uninstall 的单任务结果项(失败带 error,成功带 times)。"""

    name: str
    times: list[str] | None = None
    error: str | None = None


class ScheduleOpResult(BaseModel):
    """调度批量操作结果(全成功 OK / 部分 PARTIAL / 全败 FAILED)。"""

    result: str
    done: list[ScheduleOpItem] = []
    failed: list[ScheduleOpItem] = []
    platform: str


class JobInfo(BaseModel):
    """手动触发 job 的对外视图,对齐 ``ui.runner.Job.public()``。"""

    id: str
    domain: str
    action: str
    account: str | None = None
    argv: list[str] = []
    started_at: str | None = None
    finished_at: str | None = None
    status: str
    exit_code: int | None = None
    output: str | None = None
    result: dict[str, Any] | None = None


class JobEnvelope(BaseModel):
    """触发/查询 job 的响应信封。"""

    job: JobInfo


#%% ---------------- 应用管理(apps_api) ----------------


class AppRow(BaseModel):
    """应用清单行(注册表域 + 声明式应用)。"""

    name: str
    kind: str | None = None
    display: str
    app_name: str | None = None
    desc: str | None = None
    app_desc: str | None = None
    ready: bool | None = None
    config: dict[str, Any] | None = None
    times: list[str] = []
    enabled: bool = True
    region: str = "cn"
    credits_refresh: str = "00:00"
    source: str = "auto"
    declarative: bool = False


class AppsResponse(BaseModel):
    """应用清单响应。"""

    apps: list[AppRow] = []
    generated_at: str


class AppCreateResult(BaseModel):
    """新建/删除应用的结果(新建带 files,删除带 removed)。"""

    result: str
    domain: str
    files: int | None = None
    removed: list[str] | None = None
    message: str


class AppStateUpdateResult(BaseModel):
    """应用主档更新结果(返回更新后的主档行)。"""

    result: str
    app: dict[str, Any]
    message: str


class AppConfigView(BaseModel):
    """应用配置的字段视图(YAML 透传,非稳定契约)。"""

    domain: str
    fields: dict[str, Any]


class AppUpdateResult(BaseModel):
    """应用配置保存结果。"""

    ok: bool
    path: str
    message: str


class AppSyncItem(BaseModel):
    """应用同步成功的单条(domain/区域/登录态探测/客户端版本)。"""

    domain: str
    region: str
    profile: bool
    version: str | None = None


class AppSyncSkip(BaseModel):
    """应用同步跳过的单条(不可同步的形态,如国际版无客户端)。"""

    domain: str
    reason: str


class AppSyncFail(BaseModel):
    """应用同步失败的单条。"""

    domain: str
    error: str


class AppSyncResponse(BaseModel):
    """应用同步结果(主档自动建档/补档)。"""

    result: str
    synced: list[AppSyncItem] = []
    skipped: list[AppSyncSkip] = []
    failed: list[AppSyncFail] = []


#%% ---------------- 配置编辑(config_api) ----------------


class ConfigListItem(BaseModel):
    """配置文件清单项(域 + 是否存在)。"""

    domain: str
    exists: bool


class ConfigContent(BaseModel):
    """配置原文(YAML 文本,原样透传)。"""

    domain: str
    content: str


class OkResult(BaseModel):
    """通用布尔结果。"""

    ok: bool


class OkPathResult(BaseModel):
    """带路径的布尔结果(保存落盘位置)。"""

    ok: bool
    path: str
