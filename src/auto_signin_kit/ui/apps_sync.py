"""应用同步(P0 前身 SPEC-11 阶段三,从 apps_api.py 拆出控制行数):
探测本机已装应用 → ``ask_apps`` 建主档 + 账户档案。

被 ``ui.apps_api`` 的 ``POST /apps/sync`` 路由调用;本模块是纯业务函数,
不持 router——apps_api 管 HTTP 形状,这里管同步动作(行数门禁拆分,
见 AGENTS.md 硬性规则 7)。
"""

from __future__ import annotations

from typing import Any

from auto_signin_kit.core import store
from auto_signin_kit.core.adapters.device_info import machine_info
from auto_signin_kit.core.declarative import apps as decl_apps
from auto_signin_kit.core.declarative import authkit
from auto_signin_kit.core.declarative.schema import AppDeclaration
from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.domains.schedule.data import providers

#: 引擎/基础设施域: 代码形态不是应用,同步与列表都跳过(不建档)
ENGINE_DOMAINS = ("web_signin", "client_signin", "schedule")


def _declarative_apps() -> list[AppDeclaration]:
    """全部有效声明;坏文件不进列表(all_apps_safe 内部已给 stderr 警告)。"""
    return decl_apps.all_apps_safe()[0]


def sync_apps() -> dict[str, Any]:
    """应用同步(新用户初始化第一步): 探测本机已装应用 → ask_apps 建主档。

    逐注册表域 ``ready()`` 探测 + ``client_info()`` 采集档案,``ensure_app``
    建主档行 → ``ensure_account`` 补 default 户;未安装跳过,单域失败如实进
    failed 不中断。声明式应用走同一套主档(就绪 = authkit 登录态探测)。
    ``version``/``region`` 是本机探测的权威值(version 有值即覆盖,客户端
    升级要跟上);声明式应用没有客户端版本,如实 None。
    """
    synced: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    def ensure(self_name: str, region: str, info: dict[str, Any] | None,
               *, want_account: bool, app_name: str | None = None,
               app_desc: str | None = None, kind: str | None = None,
               credits_refresh: str | None = None,
               version: str | None = None) -> None:
        """主档 + default 户 + 档案;app_name/app_desc/kind 只补空(用户覆盖过的不动);
        version 是探测权威值,有值即覆盖。"""
        store.ensure_app(self_name, region, app_name=app_name, app_desc=app_desc,
                         kind=kind, credits_refresh_time=credits_refresh,
                         version=version)
        if want_account:
            store.ensure_account(self_name, "default")
        if info:
            store.set_account_client_info(self_name, "default",
                                          {**machine_info(), **info})

    for name, meta in providers():
        if name in ENGINE_DOMAINS:
            continue  # 引擎域不是应用,不建档
        collect = getattr(meta, "client_info", None)
        try:
            info = collect() if collect is not None else None
        except Exception as exc:
            failed.append({"domain": name, "error": str(exc)})
            continue
        try:
            ready = bool(meta.ready()) if hasattr(meta, "ready") else None
        except Exception:
            ready = None  # 探测器自身异常: 不据此判未安装,交给档案采集兜底
        if ready is False or (ready is None and not info):
            skipped.append({"domain": name, "reason": "未安装或客户端不可用"})
            continue
        region = "cn"
        version = None
        if isinstance(info, dict):
            region = str(info.pop("region", "") or "cn")  # region 进主档,不入档案
            version = info.get("version")
        try:
            ensure(name, region, info, want_account=bool(info),
                   app_name=getattr(meta, "DISPLAY_NAME", None),
                   app_desc=getattr(meta, "DESCRIPTION", None),
                   kind=getattr(meta, "KIND", None),
                   credits_refresh=getattr(meta, "CREDITS_REFRESH_TIME", None),
                   version=version)
        except AutoSignInKitError as exc:
            failed.append({"domain": name, "error": str(exc)})
            continue
        synced.append({"domain": name, "region": region,
                       "profile": bool(info), "version": version})
    for decl in _declarative_apps():
        try:
            ready = authkit.ready(decl)
        except Exception:
            ready = None
        if not ready:
            skipped.append({"domain": decl.app_code, "reason": "登录态未就绪"})
            continue
        try:
            ensure(decl.app_code, decl.region, None, want_account=True,
                   app_name=decl.display, app_desc=decl.desc or None,
                   kind=decl.kind, credits_refresh=decl.credits_refresh_time)
        except AutoSignInKitError as exc:
            failed.append({"domain": decl.app_code, "error": str(exc)})
            continue
        synced.append({"domain": decl.app_code, "region": decl.region,
                       "profile": False, "version": None})
    return {"result": "OK" if not failed else "PARTIAL",
            "synced": synced, "skipped": skipped, "failed": failed}
