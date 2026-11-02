"""FastAPI 应用工厂: /api 路由 + 可选托管 frontend/dist 静态产物(SPEC-09 §3)。

前端产物不存在时(只装了 [web] extra 没构建前端),根路径返回提示 JSON,
API 与 /api/docs 照常可用——**不做静默兜底,缺什么提示什么**。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from auto_signin_kit.ui import api

#: editable 安装时 src/auto_signin_kit/ui/app.py 的上三级就是仓库根。
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _dist_dir() -> Path | None:
    """frontend/dist 存在则返回,否则 None(前端未构建是正常状态,不是错误)。"""
    candidate = _REPO_ROOT / "frontend" / "dist"
    return candidate if candidate.is_dir() else None


def create_app() -> FastAPI:
    """组装 API 路由与(若已构建的)前端静态托管;每次调用独立实例。"""
    app = FastAPI(
        title="auto-signin-kit 管理台",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.include_router(api.router, prefix="/api")
    dist = _dist_dir()
    if dist is not None:
        # html=True: 访问 / 直接给 index.html;路由已在 /api 注册,优先命中。
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    else:
        @app.get("/", include_in_schema=False)
        def _frontend_hint() -> dict[str, Any]:
            return {
                "hint": "前端未构建,API 可用。",
                "build": "cd frontend && npm install && npm run build",
                "api_docs": "/api/docs",
            }
    return app
