"""应用管理 API(SPEC-11 阶段三): 列表 / 新建 / 删除。

新建与删除都会触发"重装生效"编排: spawn 独立重建进程(等待本 serve
退出 → uv sync → tool 重装 → 重启 serve)→ 本 serve 优雅退出。前端在
收到响应后应轮询 /api/overview 直到服务回来,再刷新列表。
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auto_signin_kit.core import store
from auto_signin_kit.core.adapters.device_info import machine_info
from auto_signin_kit.core.config import load_domain_config_model
from auto_signin_kit.core.declarative import apps as decl_apps
from auto_signin_kit.core.declarative import authkit
from auto_signin_kit.core.declarative.schema import AppDeclaration
from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.domains.schedule import service as schedule_service
from auto_signin_kit.domains.schedule.data import app_label, providers
from auto_signin_kit.domains.schedule.schema import ScheduleDomainConfig
from auto_signin_kit.scaffolding import service as scaffolding
from auto_signin_kit.scaffolding.rebuild import spawn_rebuild
from auto_signin_kit.ui import config_api, daemon

router = APIRouter()

#: 引擎/基础设施域是代码形态不是应用,不进应用列表与主档(ask_apps 只存应用)。
_ENGINE_DOMAINS = ("web_signin", "client_signin", "schedule")


class AppCreate(BaseModel):
    """新建应用请求体。"""

    name: str
    kind: str = "web"
    display: str | None = None
    desc: str | None = None
    base_url: str
    daily_time: str = "09:05"
    region: str = "cn"  # 版本区: cn / intl,落 ask_apps 主档
    credits_refresh_time: str = "00:00"  # 积分刷新时刻 HH:MM,落主档+声明


def _app_config(name: str) -> dict[str, Any] | None:
    """域 YAML 的字段视图;配置不存在时返回 None(真实状态,如实呈报)。"""
    try:
        return config_api.load_data(name)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def _schedule_times_by_domain() -> dict[str, list[str]]:
    """应用标签 → 全部任务的触发时间(已套 schedule.yaml 的任务覆盖);按 --app 归到应用编码。"""
    config = load_domain_config_model("schedule", ScheduleDomainConfig,
                                      merge_local=True)
    out: dict[str, list[str]] = {}
    for spec in schedule_service.select_tasks(config, None):
        out.setdefault(app_label(spec), []).extend(spec.times)
    return out


def _declarative_apps() -> list[AppDeclaration]:
    """全部有效声明;坏文件不进列表(all_apps_safe 内部已给 stderr 警告)。"""
    return decl_apps.all_apps_safe()[0]


@router.get("/apps")
def list_apps() -> dict[str, Any]:
    """注册表发现的应用清单;档案列以 ask_apps 主档为准(关联键 app_code = 台账 domain 列)。"""
    times_by_domain = _schedule_times_by_domain()
    apps = []
    for name, meta in providers():
        if name in _ENGINE_DOMAINS:
            continue  # 引擎域不是应用,不进列表
        ready = None
        try:
            ready = bool(meta.ready())
        except Exception:
            ready = None
        state = store.app_state(name)
        desc_default = getattr(meta, "DESCRIPTION", None)
        apps.append({
            "name": name,
            "kind": (state or {}).get("kind") or getattr(meta, "KIND", None),
            "display": (state or {}).get("app_name") or getattr(meta, "DISPLAY_NAME", name),
            "app_name": (state or {}).get("app_name"),  # 覆盖名原文(null = 未覆盖)
            "desc": (state or {}).get("app_desc") or desc_default,  # 生效描述
            "app_desc": (state or {}).get("app_desc"),  # 覆盖描述原文(null = 未覆盖)
            "ready": ready,
            "config": _app_config(name),
            "times": times_by_domain.get(name, []),
            "enabled": bool((state or {}).get("enabled", 1)),
            "region": (state or {}).get("region") or "cn",
            "credits_refresh": ((state or {}).get("credits_refresh_time")
                                or getattr(meta, "CREDITS_REFRESH_TIME", None)
                                or "00:00"),
            "source": (state or {}).get("source") or "auto",
            "declarative": False,
        })
    for decl in _declarative_apps():  # 声明式应用: 一份 YAML 一个应用
        state = store.app_state(decl.app_code)
        try:
            ready: bool | None = authkit.ready(decl)
        except Exception:
            ready = None
        apps.append({
            "name": decl.app_code,
            "kind": (state or {}).get("kind") or decl.kind,
            "display": (state or {}).get("app_name") or decl.display,
            "app_name": (state or {}).get("app_name"),
            "desc": (state or {}).get("app_desc") or decl.desc or None,
            "app_desc": (state or {}).get("app_desc"),
            "ready": ready,
            "config": decl_apps.raw_declaration(decl.app_code),
            "times": times_by_domain.get(decl.app_code, []),
            "enabled": bool((state or {}).get("enabled", 1)),
            "region": (state or {}).get("region") or decl.region,
            "credits_refresh": ((state or {}).get("credits_refresh_time")
                                or decl.credits_refresh_time),
            "source": (state or {}).get("source") or "auto",
            "declarative": True,
        })
    return {"apps": apps, "generated_at": _now()}


def _now() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def _rebuild_and_exit() -> None:
    """spawn 重建进程(等本 serve 退出 → 重装 → 重启 serve)→ 本 serve 退出。"""
    spawn_rebuild(wait_pid=os.getpid(), reopen_serve=True)
    daemon.request_shutdown()


def _create_client_declaration(body: AppCreate) -> None:
    """客户端签到新建 = 写一份声明 YAML,走 client_signin 引擎,零代码桩。

    生成的 YAML 是桩(auth 的目录/cookie 名留空,claim 路径占位): 引擎
    加载合法,就绪探测如实为 False,运行时显式报"请编辑声明"——不做
    静默兜底,填好真实参数才可跑。
    """
    from auto_signin_kit.core.errors import ConfigError

    declaration = {
        "app_code": body.name,
        "region": body.region,
        "display": body.display or body.name,
        "kind": "client",
        "desc": body.desc or "",
        "base_url": body.base_url,
        "credits_refresh_time": body.credits_refresh_time,
        "auth": {"kind": "chromium_cookies", "files": [], "cookie_names": []},
        "claim": {"method": "POST", "path": "/"},
        "schedule": {"times": [body.daily_time]},
    }
    try:
        decl_apps.save_declaration(body.name, declaration)
    except ConfigError as exc:
        raise HTTPException(422, str(exc)) from exc


def _delete_declaration(app_code: str) -> None:
    """删除一份声明 YAML(新建失败回滚用;不存在不动)。"""
    (decl_apps.apps_dir() / f"{app_code}.yaml").unlink(missing_ok=True)


@router.post("/apps")
def create_app(body: AppCreate) -> dict[str, Any]:
    """生成域骨架(网页版签)或声明 YAML(客户端签到)并后台重建生效;响应返回后服务会短暂离线。"""
    from auto_signin_kit.core.declarative.constants import valid_refresh_time

    if not valid_refresh_time(body.credits_refresh_time):
        raise HTTPException(422, "积分刷新时间必须是 HH:MM(00:00~23:59): %r"
                            % body.credits_refresh_time)
    if body.kind == "client":
        _create_client_declaration(body)
        try:
            store.ensure_app(body.name, body.region, app_name=body.display,
                             app_desc=body.desc, kind=body.kind, source="manual",
                             credits_refresh_time=body.credits_refresh_time)
            _rebuild_and_exit()
        except Exception as exc:
            _delete_declaration(body.name)
            raise HTTPException(500, "重建进程拉起失败,已回滚: %s" % exc) from exc
        return {
            "result": "OK",
            "domain": body.name,
            "message": "重建进行中(约 1 分钟): 服务将离线并自动回来,完成后新应用"
                       "出现在列表;客户端签到参数是桩,请编辑"
                       " config/apps/%s.yaml 填数据目录/cookie 名/接口路径。" % body.name,
        }
    try:
        manifest = scaffolding.create_domain(
            body.name, body.kind, body.display, body.base_url, body.daily_time)
    except AutoSignInKitError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        store.ensure_app(body.name, body.region, app_name=body.display,
                         app_desc=body.desc, kind=body.kind,
                         source="manual",  # 主档行随建档落库
                         credits_refresh_time=body.credits_refresh_time)
        _rebuild_and_exit()
    except Exception as exc:
        scaffolding.rollback(manifest)
        raise HTTPException(500, "重建进程拉起失败,已回滚: %s" % exc) from exc
    return {
        "result": "OK",
        "domain": manifest.domain,
        "files": len(manifest.files),
        "message": "重建进行中(约 1 分钟): 服务将离线并自动回来,"
                   "完成后新域出现在列表;签到逻辑是桩,测试标 xfail。",
    }


@router.delete("/apps/{name}")
def delete_app(name: str) -> dict[str, Any]:
    """物理删除一个域(前端负责输入域名二次确认)并后台重建生效。"""
    try:
        removed = scaffolding.remove_domain(name)
    except AutoSignInKitError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        _rebuild_and_exit()
    except Exception as exc:
        # 重建失败只需提示用户手动重装
        raise HTTPException(500, "已删除但重建失败(手动执行 uv sync 后重装即可): %s"
                             % exc) from exc
    return {"result": "OK", "domain": name, "removed": removed,
            "message": "重建进行中: 服务将离线并自动回来。"
                       "已注册的系统定时任务请到调度页注销。"}


class AppStateUpdate(BaseModel):
    """PUT /apps/{name}/state 请求体: 应用主档部分更新(None = 不改)。"""

    enabled: bool | None = None
    app_name: str | None = None
    app_desc: str | None = None
    credits_refresh_time: str | None = None


def _is_known_app(name: str) -> bool:
    """应用名是否可操作: 注册表域 或 声明式应用编码。"""
    if name in config_api.known_domains():
        return True
    return any(decl.app_code == name for decl in _declarative_apps())


def _is_declarative(name: str) -> bool:
    return any(decl.app_code == name for decl in _declarative_apps())


@router.put("/apps/{name}/state")
def update_app_state(name: str, body: AppStateUpdate) -> dict[str, Any]:
    """更新应用主档(总开关/显示名/描述);下次静默调度触发时生效。

    停用语义: 静默(调度)触发整个域跳过(不执行、不落库);手动执行
    永远放行。已注册的系统定时任务不注销——关开关比动 launchd 轻得多。
    """
    if not _is_known_app(name):
        raise HTTPException(404, f"未知域: {name}")
    from auto_signin_kit.core.declarative.constants import valid_refresh_time

    if (body.credits_refresh_time is not None
            and not valid_refresh_time(body.credits_refresh_time)):
        raise HTTPException(422, "积分刷新时间必须是 HH:MM 形状: %r"
                            % body.credits_refresh_time)
    row = store.set_app_state(name, enabled=body.enabled,
                              app_name=body.app_name, app_desc=body.app_desc,
                              credits_refresh_time=body.credits_refresh_time)
    return {"result": "OK", "app": row,
            "message": "已保存: 下次调度触发时生效(手动执行不受影响)"}


class AppUpdate(BaseModel):
    """PUT /apps/{name} 请求体: 只传要改的字段(部分更新,深合并)。"""

    updates: dict[str, Any]


@router.get("/apps/{name}/config")
def get_app_config(name: str) -> dict[str, Any]:
    """应用配置的字段视图(表单化编辑取数;纯读取不落盘)。"""
    if _is_declarative(name):
        return {"domain": name, "fields": decl_apps.raw_declaration(name)}
    if name not in config_api.known_domains():
        raise HTTPException(404, f"未知域: {name}")
    return {"domain": name, "fields": config_api.load_data(name)}


@router.put("/apps/{name}")
def update_app(name: str, body: AppUpdate) -> dict[str, Any]:
    """表单化编辑应用配置: 深合并 → 校验 → 原子写盘。

    常规域: pydantic 域配置校验;声明式应用: AppDeclaration 校验
    (extra=forbid,字段拼错当场报)。不触发重建: 配置在命令运行时读取,
    下次运行生效。注意重写会移除 YAML 里的注释——需要保留注释的精细
    编辑走「配置」页的 YAML 文本编辑。
    """
    if not _is_known_app(name):
        raise HTTPException(404, f"未知域: {name}")
    if not body.updates:
        raise HTTPException(422, "没有要修改的字段")
    if _is_declarative(name):
        from auto_signin_kit.core.errors import ConfigError

        merged = config_api.deep_merge(decl_apps.raw_declaration(name), body.updates)
        try:
            path = decl_apps.save_declaration(name, merged)
        except ConfigError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True, "path": str(path),
                "message": "已保存: 下次运行所属引擎命令时生效(YAML 注释已被重写移除)"}
    data = config_api.load_data(name)
    merged = config_api.deep_merge(data, body.updates)
    config_api.validate_data(name, merged)
    path = config_api.atomic_write_data(name, merged)
    return {"ok": True, "path": str(path),
            "message": "已保存: 下次运行 %s 命令时生效(YAML 注释已被重写移除)" % name}


@router.post("/apps/sync")
def sync_apps() -> dict[str, Any]:
    """应用同步(新用户初始化第一步): 探测本机已装应用 → ask_apps 建主档。

    逐注册表域 ``ready()`` 探测 + ``client_info()`` 采集档案,``ensure_app``
    建主档行 → ``ensure_account`` 补 default 户;未安装跳过,单域失败如实进
    failed 不中断。声明式应用走同一套主档(就绪 = authkit 登录态探测)。
    """
    synced: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    def ensure(self_name: str, region: str, info: dict[str, Any] | None,
               *, want_account: bool, app_name: str | None = None,
               app_desc: str | None = None, kind: str | None = None,
               credits_refresh: str | None = None) -> None:
        """主档 + default 户 + 档案;app_name/app_desc/kind 只补空(用户覆盖过的不动)。"""
        store.ensure_app(self_name, region, app_name=app_name, app_desc=app_desc,
                         kind=kind, credits_refresh_time=credits_refresh)
        if want_account:
            store.ensure_account(self_name, "default")
        if info:
            store.set_account_client_info(self_name, "default",
                                          {**machine_info(), **info})

    for name, meta in providers():
        if name in _ENGINE_DOMAINS:
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
                   credits_refresh=getattr(meta, "CREDITS_REFRESH_TIME", None))
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
