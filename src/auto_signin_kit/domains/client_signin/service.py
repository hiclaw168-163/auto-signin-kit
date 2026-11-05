"""client_signin 服务层: 客户端签到引擎(kind=client 声明式应用)的薄封装。

机制全部在 core/declarative(工厂 + 策略: 凭据适配器注册表 + 声明执行器),
本模块只把引擎钉到 ``kind=client``——凭据来自**本地客户端**的应用归这里:
ss_aes 解桌面端存储(trae_cn)、chromium_cookies 解浏览器数据目录(声明式
客户端应用),以及 ``apps/`` 下的 CDP 代码扩展(doubao_cn/workbuddy_cn/
qoder,扩展契约见 STD-07)。
"""

from __future__ import annotations

from typing import Any

from auto_signin_kit.core.declarative import service as engine

#: 本引擎接管的声明类别(分类标准: 凭据来源,见 STD-07)。
KIND = "client"


def domain_for(app_code: str) -> engine.DeclarativeSigninDomain:
    """编码 → 运行期域对象(类别不符显式报错)。"""
    return engine.domain_for(app_code, kind=KIND)


def run_action(
    action: str, config: Any, *, app: str = "", silent: bool = False,
    dry_run: bool = False, once: bool = False, command: str | None = None,
    account: str = "default",
) -> int:
    """引擎统一入口: ``--app`` 指定声明应用,其余交共享引擎。"""
    return engine.run_action(action, config, app=app, kind=KIND, silent=silent,
                             dry_run=dry_run, once=once, command=command,
                             account=account)


def list_lines() -> list[str]:
    """``list`` 命令的行渲染(只列 kind=client 的声明)。"""
    return engine.list_lines(KIND)
