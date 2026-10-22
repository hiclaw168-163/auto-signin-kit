"""schedule 域的任务清单: 要注册哪些定时任务、默认什么时候跑。

命令行(``argv``)固定写死在这里——它是代码结构,不是可调参数。
触发时间与执行时限的**默认值**也在这里,但可以被
``config/domains/schedule.yaml`` 的 ``tasks`` 覆盖(见 :func:`apply_overrides`):
日常调时间改配置即可,不必动代码。清单保留默认值是为了让"只改一个任务"
的用户不必把整张表抄一遍,也作为配置缺失时的兜底。

两条从原项目(``NOTE-01`` §5)承袭下来的设计约束,改这张表前务必先读:

1. **高密度触发 + 幂等领取 = 兜底单点失败**。每个任务都挂多个触发时间,不是
   为了多领(领取接口幂等,重复调用只返回"已领"),而是因为触发时刻机器可能
   关机/断网/刚开机网络没就绪。一天只有一次机会的话,撞上就等于当天断签。
   所以**不要"优化"掉看起来多余的时间点**。
2. **平台的执行时限必须大于脚本自己的时间预算**。各域预算: workbuddy 主入口
   ≤540s / 轮询 ≤240s(见 ``config/domains/workbuddy.yaml``),qoder、trae
   均 ≤240s。时限留了余量(PT10M / PT5M),改预算时同步改这里的时限。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from auto_signin_kit.core.ports.scheduler import ScheduleSpec

#: 定时任务清单;``name`` 是跨平台共用的任务标识。
TASKS: tuple[ScheduleSpec, ...] = (
    ScheduleSpec(
        name="WorkBuddyAutoSignin",
        argv=("auto-signin-kit", "workbuddy", "auto", "--silent"),
        times=("00:05",),
        limit_minutes=10,
        description="WorkBuddy 每日签到 + 成长中心(每日主入口)",
    ),
    ScheduleSpec(
        name="WorkBuddyGrowthPoll",
        argv=("auto-signin-kit", "workbuddy", "poll"),
        times=("01:05", "09:05", "17:05", "21:05"),
        limit_minutes=5,
        description="WorkBuddy 成长中心轮询(每次先查签到,兼做主入口的兜底)",
    ),
    ScheduleSpec(
        name="WorkBuddyActiveCheck",
        argv=("auto-signin-kit", "workbuddy", "active", "--silent"),
        times=("12:05", "20:05"),
        limit_minutes=5,
        description="WorkBuddy 活跃保底: 查今日活跃,没有就自动补一次对话(活跃天数只认对话)",
    ),
    ScheduleSpec(
        name="QoderDailyCredits",
        argv=("auto-signin-kit", "qoder", "auto", "--silent"),
        times=("10:05", "11:05", "15:05", "19:05", "23:05"),
        limit_minutes=5,
        description="Qoder CN 每日 Credits 领取",
    ),
    ScheduleSpec(
        name="TraeDailyCheckin",
        argv=("auto-signin-kit", "trae", "auto", "--silent"),
        times=("09:05", "13:05", "15:05", "19:05", "21:05", "23:05"),
        limit_minutes=5,
        description="Trae CN 每日签到(1024 活动期限流,高密度触发兜排队)",
    ),
)


def domain_of(spec: ScheduleSpec) -> str:
    """从命令行里取出域名(argv 形如 ``auto-signin-kit <域> <子命令>``)。"""
    return spec.argv[1] if len(spec.argv) > 1 else ""


def known_domains() -> tuple[str, ...]:
    """清单里出现过的域名,去重后按出现顺序返回(用于报错提示)。"""
    out: list[str] = []
    for spec in TASKS:
        name = domain_of(spec)
        if name and name not in out:
            out.append(name)
    return tuple(out)


def tasks_for(domain: str | None) -> tuple[ScheduleSpec, ...]:
    """按域名过滤;``None``/空串表示全部。未知域名返回空元组(由 service 报错)。"""
    if not domain:
        return TASKS
    return tuple(spec for spec in TASKS if domain_of(spec) == domain)


def apply_overrides(spec: ScheduleSpec, override: Any) -> ScheduleSpec:
    """把配置里的 ``times`` / ``limit_minutes`` 覆盖到清单项上。

    ``override`` 为 ``None`` 或某字段没给时沿用清单默认值——这样只想改一个
    任务的用户不必把整张时间表抄一遍,而清单仍能作为"配置缺失时的兜底"。
    """
    if override is None:
        return spec
    times = tuple(override.times) if getattr(override, "times", None) else spec.times
    limit = getattr(override, "limit_minutes", None)
    return replace(
        spec,
        times=times,
        limit_minutes=spec.limit_minutes if limit is None else limit,
    )
