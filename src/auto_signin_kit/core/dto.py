"""统一 DTO(数据传输对象)——全项目唯一的 DTO 定义处。

背景(2026-11-05 规范化): service 返回与台账读查此前都是裸 dict,字段约定
散落在各模块注释里,没有单一事实来源。按"分层对象"规约收口到本模块,
用到的地方直接 import:

- **行 DTO**(``*Row``): 台账三张表/视图的行形状。TypedDict 是 dict 语义,
  ``row["col"]`` 读法与 ``sqlite3.Row`` 无缝互换,存量读方零改动;
- **运行结果 DTO**(:class:`RunOutcome`): 各域 service 的统一返回信封。
  公共字段(result/needs_attention/report/account...)是跨域统一契约,
  域特有字段(campaign/credits/...)按域补进信封,最终整体落台账
  request/response 两列。

统一审计字段 ``created_at``/``updated_at`` 是所有表必备(DDL 见 ``sql/*.sql``):
行创建/最近更新时间,本地无时区,写层维护。

只放"形状",不放行为: DTO 无方法无逻辑,业务动作归 service/kit。
"""

from __future__ import annotations

from typing import Any, TypedDict


class SigninRunRow(TypedDict, total=False):
    """``ask_signin_runs`` 行(签到运行台账,一行一次运行)。"""

    id: int
    domain: str
    account_name: str | None
    executed_at: str
    operation: str | None
    source: str
    result: str
    needs_attention: int
    report: str | None
    request: str | None
    response: str | None
    detail: str
    account: str | None
    created_at: str
    updated_at: str


class ScheduleChangeRow(TypedDict, total=False):
    """``ask_schedule_changes`` 行(调度变更审计,只插不改)。"""

    id: int
    executed_at: str
    operation: str
    platform: str | None
    tasks: str | None
    report: str | None
    created_at: str
    updated_at: str


class AccountRow(TypedDict, total=False):
    """``ask_accounts`` 行(账户维表,积分业务的主体维度)。"""

    id: int
    domain: str
    name: str
    enabled: int
    uid: str | None
    device_id: str | None
    client_version: str | None
    daily_potential: int | None
    install_path: str | None
    client_info: str | None
    info_synced_at: str | None
    created_at: str
    updated_at: str


class DailyCheckinRow(TypedDict, total=False):
    """``ask_daily_checkins`` 视图行(每天每域每账户的最终状态)。"""

    checkin_day: str
    domain: str
    account_name: str | None
    result: str
    source: str
    needs_attention: int
    report: str | None
    executed_at: str


class RunOutcome(TypedDict, total=False):
    """各域 service 的统一返回信封(emit_result 落库前的形状)。

    必有语义: ``result`` 标准词表(core.report 10 词)、``needs_attention``
    是否需要人管、``report`` 一句人话。其余公共字段按动作透传;域特有字段
    (如 qoder 的 campaigns、workbuddy 的 credits)也进这个信封——
    台账把 request= 显式入参收 request 列,其余收 response 列。
    """

    result: str
    needs_attention: bool
    report: str
    #: 以下为跨域公共透传字段
    account: str
    domain: str
    action: str
    request: dict[str, Any] | None
    token_expires_at: str
    #: 以下为域特有字段(信封允许多态)
    campaign: str | None
    campaigns: list[dict[str, Any]]
    credits: int | float | None
    http: int
