"""web_signin 服务层: 网页版签引擎(kind=web 声明式应用)的薄封装。

机制全部在 core/declarative(工厂 + 策略: 凭据适配器注册表 + 声明执行器),
本模块只把引擎钉到 ``kind=web``——凭据来自**手动维护的令牌文件/环境变量**
(plain 适配器)的应用归这里;凭据来自本地客户端的(ss_aes/chromium_cookies/
CDP)归 client_signin 引擎。
"""

from __future__ import annotations

from typing import Any

from auto_signin_kit.core.declarative import service as engine

#: 本引擎接管的声明类别(分类标准: 凭据来源,见 STD-07)。
KIND = "web"


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
    """``list`` 命令的行渲染(只列 kind=web 的声明)。"""
    return engine.list_lines(KIND)
