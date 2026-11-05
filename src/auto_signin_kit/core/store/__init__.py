"""SQLite 台账层: 签到结果与调度变更的规整落库。

表设计(统一 ``ask_`` 前缀 = auto-signin-kit,同类对象一眼归属;
刻意不用 ``events`` 之类笼统大表,每张表见名知义):

- ``ask_signin_runs``      **签到运行台账**: 每次签到/领取/活跃保底命令的运行结果,
                           一行一次运行,是"这周有没有断签"这类问题的数据源
- ``ask_schedule_changes`` **调度变更审计**: 定时任务的注册/注销动作记录,
                           回答"任务是什么时候被谁改成的什么时间"
- ``ask_accounts``         **账户维表**: 哪个域的哪个账户、每日可获取积分上限,
                           积分业务的主体维度(每域当前一户 default,支持多户)
- ``ask_daily_checkins``   **视图**(非实体表): 每天每域最后一次运行 = 当天最终状态,
                           把"一天多次运行"折叠成一行,Web 台账页与人工查库都读它
- ``ask_daily_credits``    **视图**(非实体表): 每天每域每账户的积分所得合计,
                           从成功行 response JSON 现算,派生数据不落库

结构(2026-11-03 按表拆分,扩表路径见各模块 docstring):

- ``connection.py``   连接建立/库文件定位(所有表操作的共同入口)
- ``ingest.py``       落库分流: emit_result 事件按域进对应表(best-effort)
- ``runs.py``         ask_signin_runs 的读查与维护(台账/总览/跳过判断/清理)
- ``credits.py``      积分与账户: 每日所得/账户活跃/上限对比三个业务问题
- ``maintenance.py``  跨表整理: 自增 id 重排等偶尔手动触发的操作

新增一张表的固定三步: ``store_migrations`` 加 DDL/索引 → 按表新建读查
模块 → 本包出口;跨表整理动作进 ``maintenance.py`` 并登记表清单。

写入是 **best-effort**(见 ``ingest.py``): 台账永远不能影响签到本身,
任何数据库错误都在本层静默吞掉。
"""

from auto_signin_kit.core.store.apps import app_enabled, app_state, ensure_app, set_app_state
from auto_signin_kit.core.store.connection import LEDGER_FILENAME, connect, db_path
from auto_signin_kit.core.store.credits import (
    accounts_summary,
    capacity_summary,
    credits_daily,
    credits_stats,
    ensure_account,
    set_account_client_info,
    set_account_enabled,
    set_account_potential,
)
from auto_signin_kit.core.store.ingest import SCHEDULE_DOMAIN, record_payload
from auto_signin_kit.core.store.maintenance import resequence_ids
from auto_signin_kit.core.store.runs import (
    SIGNIN_DOMAINS,
    SIGNIN_DONE_RESULTS,
    compact_history,
    daily_status,
    done_today,
    events,
    failure_count_today,
    overview_by_account,
    overview_stats,
    signed_today_by_domain,
)

__all__ = [
    "LEDGER_FILENAME",
    "SCHEDULE_DOMAIN",
    "SIGNIN_DOMAINS",
    "SIGNIN_DONE_RESULTS",
    "accounts_summary",
    "app_enabled",
    "app_state",
    "capacity_summary",
    "compact_history",
    "connect",
    "credits_daily",
    "credits_stats",
    "daily_status",
    "db_path",
    "done_today",
    "ensure_account",
    "ensure_app",
    "events",
    "failure_count_today",
    "overview_by_account",
    "overview_stats",
    "record_payload",
    "resequence_ids",
    "set_account_client_info",
    "set_account_enabled",
    "set_account_potential",
    "set_app_state",
    "signed_today_by_domain",
]
