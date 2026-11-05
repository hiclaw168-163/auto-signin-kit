"""web_signin 的调度声明: 扫描 ``config/apps/*.yaml`` 动态生成任务清单。

与代码域的差异: 声明来源是用户可编辑的 YAML 而非模块常量——
- 新增应用 = 放一份 YAML + 重跑 ``schedule install``(entry_points 不动);
- 多账户展开在本模块完成(**不声明** ``accounts()`` 钩子,避免 schedule
  域的 expand_accounts 二次展开): 每个具名账户一个 ``@账户`` 实例,
  触发时间整体 +i 分钟错峰,与全库口径一致;
- 坏 YAML 在 import 期只警告不炸(:func:`all_apps_safe`),真实运行时
  会严格报错;``schedule install`` 前用 ``web_signin list`` 看全貌。
"""

from __future__ import annotations

from dataclasses import replace

from auto_signin_kit.core.ports.scheduler import ScheduleSpec
from auto_signin_kit.domains.web_signin.apps import all_apps_safe

DISPLAY_NAME = "Web 应用(声明式)"


def _task_name(app_code: str) -> str:
    """app_code → launchd 安全的任务名: ``trae`` → ``WebSigninTrae``。

    蛇形进 launchd label 没问题,但冒号等符号有歧义;对齐既有任务名惯例
    (DoubaoDailyCheckin 式 CamelCase),账户实例仍按全库口径 ``@账户``。
    """
    return "WebSignin" + "".join(part.capitalize() for part in app_code.split("_"))


def _shift_time(hhmm: str, minutes: int) -> str:
    """``HH:MM`` 平移 N 分钟(可跨小时/跨天回绕);与 schedule 域同口径。"""
    hour, minute = (int(part) for part in hhmm.split(":", 1))
    total = (hour * 60 + minute + minutes) % (24 * 60)
    return "%02d:%02d" % divmod(total, 60)


def _specs() -> tuple[ScheduleSpec, ...]:
    """每应用一个基础任务(default 户)+ 每具名账户一个错峰实例。"""
    out: list[ScheduleSpec] = []
    for decl in all_apps_safe()[0]:
        argv = ("auto-signin-kit", "web_signin", "run", "--app", decl.app_code, "--silent")
        base = ScheduleSpec(
            name=_task_name(decl.app_code),
            argv=argv,
            times=tuple(decl.schedule.times),
            limit_minutes=decl.schedule.limit_minutes,
            description="%s 每日%s(声明式)" % (decl.display, decl.ready_noun),
        )
        out.append(base)
        enabled = [ref for ref in decl.accounts if ref.enabled]
        for index, ref in enumerate(enabled, start=1):
            out.append(replace(
                base,
                name="%s@%s" % (base.name, ref.name),
                argv=(*argv, "--account", ref.name),
                times=tuple(_shift_time(t, index) for t in base.times),
                description="%s · 账户 %s" % (base.description, ref.name),
            ))
    return tuple(out)


SPECS: tuple[ScheduleSpec, ...] = _specs()


def app_label(spec: ScheduleSpec) -> str:
    """任务归属标签(扩展点,schedule 域的 data.app_label 调用):
    引擎任务按 ``--app`` 归到应用编码——台账/主档/UI 分组全按 app_code 对齐。
    """
    return spec.argv[spec.argv.index("--app") + 1]


def ready() -> bool:
    """就绪探测(应用同步用): 任一声明应用登录态就绪即 True。"""
    from auto_signin_kit.domains.web_signin import authkit
    from auto_signin_kit.domains.web_signin.schema import AppDeclaration

    return any(authkit.ready(decl) for decl in all_apps_safe()[0]
               if isinstance(decl, AppDeclaration))
