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
from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.domains.schedule import service as schedule_service
from auto_signin_kit.domains.schedule.data import domain_of, providers
from auto_signin_kit.domains.schedule.schema import ScheduleDomainConfig
from auto_signin_kit.scaffolding import service as scaffolding
from auto_signin_kit.scaffolding.rebuild import spawn_rebuild
from auto_signin_kit.ui import config_api, daemon

router = APIRouter()


class AppCreate(BaseModel):
    """新建应用请求体。"""

    name: str
    kind: str = "web"
    display: str | None = None
    base_url: str
    daily_time: str = "09:05"


def _app_config(name: str) -> dict[str, Any] | None:
    """域 YAML 的字段视图;配置不存在时返回 None(真实状态,如实呈报)。"""
    try:
        return config_api.load_data(name)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def _schedule_times_by_domain() -> dict[str, list[str]]:
    """域名 → 该域全部任务的触发时间(已套 schedule.yaml 的任务覆盖)。"""
    config = load_domain_config_model("schedule", ScheduleDomainConfig,
                                      merge_local=True)
    out: dict[str, list[str]] = {}
    for spec in schedule_service.select_tasks(config, None):
        out.setdefault(domain_of(spec), []).extend(spec.times)
    return out


@router.get("/apps")
def list_apps() -> dict[str, Any]:
    """注册表发现的应用清单(编码/类别/显示名/就绪探测/配置字段/调度时间/主档)。

    主档来自 ask_apps(总开关/版本区/显示名覆盖/描述),行惰性补齐: 从未
    操作过的应用按"启用 + 注册表展示名"呈现,不预先造行。关联键:
    应用的 app_code = 台账各表的 domain 列值。
    """
    times_by_domain = _schedule_times_by_domain()
    apps = []
    for name, meta in providers():
        ready = None
        try:
            ready = bool(meta.ready())
        except Exception:
            ready = None
        state = store.app_state(name)
        apps.append({
            "name": name,
            "kind": getattr(meta, "KIND", None),
            "display": (state or {}).get("app_name") or getattr(meta, "DISPLAY_NAME", name),
            "app_name": (state or {}).get("app_name"),  # 覆盖名原文(null = 未覆盖)
            "ready": ready,
            "config": _app_config(name),
            "times": times_by_domain.get(name, []),
            "enabled": bool((state or {}).get("enabled", 1)),
            "region": (state or {}).get("region") or "cn",
            "app_desc": (state or {}).get("app_desc"),
        })
    return {"apps": apps, "generated_at": _now()}


def _now() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def _rebuild_and_exit() -> None:
    """spawn 重建进程(等本 serve 退出 → 重装 → 重启 serve)→ 本 serve 退出。"""
    spawn_rebuild(wait_pid=os.getpid(), reopen_serve=True)
    daemon.request_shutdown()


@router.post("/apps")
def create_app(body: AppCreate) -> dict[str, Any]:
    """生成域骨架并后台重建生效;响应返回后本服务会短暂离线。"""
    try:
        manifest = scaffolding.create_domain(
            body.name, body.kind, body.display, body.base_url, body.daily_time)
    except AutoSignInKitError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
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


@router.put("/apps/{name}/state")
def update_app_state(name: str, body: AppStateUpdate) -> dict[str, Any]:
    """更新应用主档(总开关/显示名/描述);下次静默调度触发时生效。

    停用语义: 静默(调度)触发整个域跳过(不执行、不落库);手动执行
    永远放行。已注册的系统定时任务不注销——关开关比动 launchd 轻得多。
    """
    if name not in config_api.known_domains():
        raise HTTPException(404, f"未知域: {name}")
    row = store.set_app_state(name, enabled=body.enabled,
                              app_name=body.app_name, app_desc=body.app_desc)
    return {"result": "OK", "app": row,
            "message": "已保存: 下次调度触发时生效(手动执行不受影响)"}


class AppUpdate(BaseModel):
    """PUT /apps/{name} 请求体: 只传要改的字段(部分更新,深合并)。"""

    updates: dict[str, Any]


@router.get("/apps/{name}/config")
def get_app_config(name: str) -> dict[str, Any]:
    """应用配置的字段视图(表单化编辑取数;纯读取不落盘)。"""
    if name not in config_api.known_domains():
        raise HTTPException(404, f"未知域: {name}")
    return {"domain": name, "fields": config_api.load_data(name)}


@router.put("/apps/{name}")
def update_app(name: str, body: AppUpdate) -> dict[str, Any]:
    """表单化编辑应用配置: 深合并 → pydantic 校验 → 原子写盘。

    不触发重建: 配置在命令运行时读取,下次运行生效。注意重写会移除
    YAML 里的注释——需要保留注释的精细编辑走「配置」页的 YAML 文本编辑。
    """
    if name not in config_api.known_domains():
        raise HTTPException(404, f"未知域: {name}")
    if not body.updates:
        raise HTTPException(422, "没有要修改的字段")
    data = config_api.load_data(name)
    merged = config_api.deep_merge(data, body.updates)
    config_api.validate_data(name, merged)
    path = config_api.atomic_write_data(name, merged)
    return {"ok": True, "path": str(path),
            "message": "已保存: 下次运行 %s 命令时生效(YAML 注释已被重写移除)" % name}


@router.post("/apps/client-info/sync")
def sync_client_info() -> dict[str, Any]:
    """同步各域客户端档案: 采集 → 落 ask_accounts.client_info。

    采集走注册表声明(各域 schedule_meta.client_info,可选): 逐域调用,
    单域失败如实进 failed,不中断其他域;采集结果合并本机 machine_info
    (hostname/OS/MachineGuid 脱敏)。账户未登记的域自动补 default 户。
    """
    done: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for name, meta in providers():
        collect = getattr(meta, "client_info", None)
        if collect is None:
            continue
        try:
            info = collect()
        except Exception as exc:
            failed.append({"domain": name, "error": str(exc)})
            continue
        if not info:
            failed.append({"domain": name, "error": "客户端未安装/信息不可得"})
            continue
        try:
            store.ensure_account(name, "default")
            store.set_account_client_info(name, "default",
                                          {**machine_info(), **info})
        except AutoSignInKitError as exc:
            failed.append({"domain": name, "error": str(exc)})
            continue
        done.append({"domain": name, "info": info})
    return {"result": "OK" if not failed else "PARTIAL",
            "done": done, "failed": failed}
