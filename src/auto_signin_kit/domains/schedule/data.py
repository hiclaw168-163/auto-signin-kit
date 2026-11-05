"""schedule 域的任务清单来源: 各域自声明,schedule 不再硬编码任务表。

**依赖方向**(2026-11 重构): 任务定义、触发时间、客户端就绪探测全部归属
各域的 ``schedule_meta.py``,经 ``auto_signin_kit.schedule_meta`` entry-point
注册表被发现——schedule 域不 import 任何业务域,新增平台零改动本域
(Hollywood principle: 域声明自己怎么被调度,而不是调度认识域)。

触发时间与执行时限的**默认值**在各域声明,可被
``config/domains/schedule.yaml`` 的 ``tasks`` 覆盖(:func:`apply_overrides`):
日常调时间改配置即可,不必动代码。声明保留默认值是为了让"只改一个任务"
的用户不必把整张表抄一遍,也作为配置缺失时的兜底。

两条从原项目(``NOTE-01`` §5)承袭下来的设计约束,改各域声明前务必先读:

1. **高密度触发 + 幂等领取 = 兜底单点失败**。每个任务都挂多个触发时间,不是
   为了多领(领取接口幂等,重复调用只返回"已领"),而是因为触发时刻机器可能
   关机/断网/刚开机网络没就绪。一天只有一次机会的话,撞上就等于当天断签。
   所以**不要"优化"掉看起来多余的时间点**。
2. **平台的执行时限必须大于脚本自己的时间预算**。各域预算: workbuddy 主入口
   ≤540s / 轮询 ≤240s(见 ``config/domains/workbuddy.yaml``),qoder、trae
   均 ≤240s,doubao ≤60s。时限留了余量(PT10M / PT5M),改预算时同步改声明。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from functools import lru_cache
from importlib.metadata import entry_points
from types import ModuleType
from typing import Any

from auto_signin_kit.core.errors import ConfigMissingError
from auto_signin_kit.core.ports.scheduler import ScheduleSpec

#: 各域调度声明的 entry-point 组;值指向 ``<域>/schedule_meta.py`` 模块,
#: 模块契约: ``SPECS``(任务元组)/ ``DISPLAY_NAME``(展示名)/ ``ready``(探测,可为 None)。
META_GROUP = "auto_signin_kit.schedule_meta"


@lru_cache(maxsize=1)
def providers() -> tuple[tuple[str, ModuleType], ...]:
    """(域名, 声明模块) 注册表;按 entry-point 名排序保证输出顺序稳定。"""
    eps = sorted(entry_points(group=META_GROUP), key=lambda ep: ep.name)
    return tuple((ep.name, ep.load()) for ep in eps)


def provider_of(domain: str) -> ModuleType | None:
    """域名 → 该域的调度声明模块;未声明调度元数据的域返回 ``None``。"""
    for name, module in providers():
        if name == domain:
            return module
    return None


def all_specs() -> tuple[ScheduleSpec, ...]:
    """汇总所有域声明的任务;顺序 = 注册表顺序(稳定,测试与展示依赖它)。"""
    specs: list[ScheduleSpec] = []
    for _, module in providers():
        specs.extend(module.SPECS)
    return tuple(specs)


#: 全量任务清单(模块导入时发现一次;测试与 status 展示依赖其稳定性)。
TASKS: tuple[ScheduleSpec, ...] = all_specs()


def domain_of(spec: ScheduleSpec) -> str:
    """从命令行里取出域名(argv 形如 ``auto-signin-kit <域> <子命令>``)。"""
    return spec.argv[1] if len(spec.argv) > 1 else ""


def app_label(spec: ScheduleSpec) -> str:
    """任务归属的应用标签(UI 分组/应用同步用)。

    常规域 = 域名;web_signin 引擎的任务(一个域承载 N 个声明式应用)
    归属到 ``--app`` 的应用编码——引擎在其 schedule_meta 里声明
    ``app_label(spec)`` 扩展点,这里只认扩展点,不 import 业务域。
    """
    module = provider_of(domain_of(spec))
    labeler = getattr(module, "app_label", None)
    return labeler(spec) if callable(labeler) else domain_of(spec)


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


def _shift_time(hhmm: str, minutes: int) -> str:
    """``HH:MM`` 平移 N 分钟(可跨小时/跨天回绕);错峰用,分钟粒度足够。"""
    hour, minute = (int(part) for part in hhmm.split(":", 1))
    total = (hour * 60 + minute + minutes) % (24 * 60)
    return "%02d:%02d" % divmod(total, 60)


def expand_accounts(specs: tuple[ScheduleSpec, ...]) -> tuple[ScheduleSpec, ...]:
    """把各域声明的多账户展开成"基础任务 + 每账户一个实例"(SPEC-12 P3)。

    - **基础任务原样保留**: default 账户骑在原声明上(名字/argv/时间都不动),
      单账户配置零变化;账户实例名 ``<原名>@<账户>``,argv 追加 ``--account``;
    - **错峰**: 第 i 个具名账户的全部触发时间整体 +i 分钟(回绕)——多个账户
      同一秒打同一个服务端容易被当成风控流量,错开比并发礼貌;
    - 账户名单来自各域 ``schedule_meta.accounts()``(读各域 YAML 的
      ``accounts[].enabled``);未声明该函数的域视为单账户,行为不变;
    - 各域配置**还没建**时降级为单账户(域 CLI 真跑时自会把"配置不存在"
      报给用户,注册侧不连坐);**格式/校验错**仍然当场炸——schedule install
      是用户主动操作,坏配置必须显式报出来,绝不静默少注册。
    """
    out: list[ScheduleSpec] = []
    for spec in specs:
        out.append(spec)
        module = provider_of(domain_of(spec))
        names = getattr(module, "accounts", None)
        if names is None:
            continue
        try:
            declared = names()
        except ConfigMissingError:
            continue  # 域配置还没建: 单账户降级,基础任务照常注册
        for index, name in enumerate(declared, start=1):
            out.append(replace(
                spec,
                name="%s@%s" % (spec.name, name),
                argv=(*spec.argv, "--account", name),
                times=tuple(_shift_time(t, index) for t in spec.times),
                description="%s · 账户 %s" % (spec.description, name),
            ))
    return tuple(out)


def account_of(spec: ScheduleSpec) -> str:
    """任务实例的账户名(名字含 ``@账户`` 的具名实例);基础任务返回 default。"""
    return spec.name.rsplit("@", 1)[1] if "@" in spec.name else "default"


def apply_overrides(spec: ScheduleSpec, override: Any) -> ScheduleSpec:
    """把配置里的 ``times`` / ``limit_minutes`` 覆盖到声明项上。

    ``override`` 为 ``None`` 或某字段没给时沿用声明默认值——这样只想改一个
    任务的用户不必把整张时间表抄一遍,而声明仍能作为"配置缺失时的兜底"。
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


def _reset_providers_cache() -> None:
    """测试辅助: 清掉注册表缓存(测试注入假声明模块后用)。"""
    providers.cache_clear()


# entry-point 发现失败时给出可操作的报错(import 时就炸,别等到 install 半途)
if not TASKS:
    sys.stderr.write(
        "警告: schedule_meta 注册表为空——新装环境需执行"
        " `uv sync && uv tool install --editable '.[web]' --force` 刷新 entry-points\n"
    )
