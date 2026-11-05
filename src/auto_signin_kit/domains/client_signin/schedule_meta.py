"""client_signin 的调度声明: 扫描 ``config/apps/*.yaml`` 生成 kind=client 任务。

与 web_signin 引擎的 schedule_meta 同构(多账户错峰展开、坏 YAML 只警告),
差异是接管范围: 只为 ``kind: client`` 的声明生成任务,argv 走
``client_signin run --app``。CDP 代码扩展(doubao_cn/workbuddy_cn/qoder)
保留各自的 schedule_meta 入口(应用扩展契约,见 STD-07)——调度按应用
粒度安装/卸载的体验不变。
"""

from __future__ import annotations

from dataclasses import replace

from auto_signin_kit.core.declarative.apps import all_apps_safe
from auto_signin_kit.core.ports.scheduler import ScheduleSpec

DISPLAY_NAME = "客户端应用(声明式)"
#: 域类别(凭据来源口径);引擎只接管 kind=client 的声明。
KIND = "client"
#: 应用描述(同步时自动补 ask_apps.app_desc,只补空)。
DESCRIPTION = "客户端签到引擎(本地客户端凭据,一份 YAML 接入一个应用)"
#: 积分刷新时刻: 声明式应用按声明值落库(见 AppDeclaration),引擎常量供兜底。
CREDITS_REFRESH_TIME = "00:00"


def _task_name(app_code: str) -> str:
    """app_code → launchd 安全的任务名: ``trae_cn`` → ``ClientSigninTraeCn``。

    与 web_signin 引擎同口径,前缀区分引擎;账户实例仍按全库口径 ``@账户``。
    """
    return "ClientSignin" + "".join(part.capitalize() for part in app_code.split("_"))


def _shift_time(hhmm: str, minutes: int) -> str:
    """``HH:MM`` 平移 N 分钟(可跨小时/跨天回绕);与 schedule 域同口径。"""
    hour, minute = (int(part) for part in hhmm.split(":", 1))
    total = (hour * 60 + minute + minutes) % (24 * 60)
    return "%02d:%02d" % divmod(total, 60)


def _specs() -> tuple[ScheduleSpec, ...]:
    """每应用一个基础任务(default 户)+ 每具名账户一个错峰实例。"""
    out: list[ScheduleSpec] = []
    for decl in all_apps_safe()[0]:
        if decl.kind != KIND:
            continue  # 令牌文件凭据的声明由 web_signin 引擎接管
        argv = ("auto-signin-kit", "client_signin", "run", "--app", decl.app_code, "--silent")
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
    from auto_signin_kit.core.declarative import authkit

    return any(authkit.ready(decl) for decl in all_apps_safe()[0]
               if decl.kind == KIND)
