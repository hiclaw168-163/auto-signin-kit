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

from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.domains.schedule.data import providers
from auto_signin_kit.scaffolding import service as scaffolding
from auto_signin_kit.scaffolding.rebuild import spawn_rebuild
from auto_signin_kit.ui import daemon

router = APIRouter()


class AppCreate(BaseModel):
    """新建应用请求体。"""

    name: str
    kind: str = "web"
    display: str | None = None
    base_url: str
    daily_time: str = "09:05"


@router.get("/apps")
def list_apps() -> dict[str, Any]:
    """注册表发现的应用清单(名称/类别/显示名/就绪探测),零硬编码。"""
    apps = []
    for name, meta in providers():
        ready = None
        try:
            ready = bool(meta.ready())
        except Exception:
            ready = None
        apps.append({
            "name": name,
            "kind": getattr(meta, "KIND", None),
            "display": getattr(meta, "DISPLAY_NAME", name),
            "ready": ready,
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
