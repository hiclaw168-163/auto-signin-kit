"""qoder_cn(国内版)的调度声明: 与国际版对等的另一行应用、另一条定时任务。

契约(:data:`SPECS` / :data:`DISPLAY_NAME` / :data:`ready`)见
workbuddy/schedule_meta.py 的模块注释;本机两版客户端是两个应用,所以各注册
一份调度(任务名不同,互不影响),领取实现共用 :mod:`...apps.qoder`。
"""

from __future__ import annotations

from auto_signin_kit.core.config import load_domain_config_model
from auto_signin_kit.core.ports.scheduler import ScheduleSpec
from auto_signin_kit.domains.client_signin.apps.qoder.auth import is_installed
from auto_signin_kit.domains.client_signin.apps.qoder.client_info import client_info as _collect
from auto_signin_kit.domains.client_signin.apps.qoder.profile import CN
from auto_signin_kit.domains.client_signin.apps.qoder.schema import QoderDomainConfig

DISPLAY_NAME = CN.display
#: 域类别(web=网页版签 / client=客户端签到);UI 类别列与同步档案用。
KIND = "client"
#: 应用描述(同步时自动补 ask_apps.app_desc,只补空)。
DESCRIPTION = "Qoder 国内版桌面端每日签到(本地凭据解锁 + 风控头)"
#: 积分刷新时刻: 与国际版同构,过 10:00 才能领当日 Credits。
CREDITS_REFRESH_TIME = "10:00"


def client_info() -> dict | None:
    """本身份的客户端档案(应用同步按它建 region/版本/目录)。"""
    return _collect(CN)


def accounts() -> tuple[str, ...]:
    """声明且启用的具名账户(SPEC-12;default 骑基础声明)。"""
    config = load_domain_config_model(CN.app_code, QoderDomainConfig, merge_local=True)
    return tuple(ref.name for ref in config.accounts
                 if ref.name != "default" and ref.enabled)


def ready() -> bool:
    """客户端就绪探测: 本机装了国内版客户端才允许注册调度。"""
    return is_installed(CN)


SPECS: tuple[ScheduleSpec, ...] = (
    ScheduleSpec(
        name="QoderCnDailyCredits",
        argv=("auto-signin-kit", CN.app_code, "auto", "--silent"),
        times=("10:05", "11:05", "15:05", "19:05", "23:05"),
        limit_minutes=5,
        description="Qoder 国内版每日 Credits 领取",
    ),
)
