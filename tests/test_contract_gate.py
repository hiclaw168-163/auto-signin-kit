"""契约冻结门禁(P0 契约层,PROPOSAL-01): 服务端契约与冻结物必须一致。

冻结物 = ``frontend/packages/api-client/openapi.json``(五端共享的单一
事实源)。改了 ``ui/contracts.py``/路由而忘了重新生成时,本测试挡住:
契约漂移会在 pytest 层暴露,而不是在前端运行期才炸。

重新冻结: ``cd frontend && npm run gen:api``。
"""

from __future__ import annotations

import json
from pathlib import Path

from auto_signin_kit.ui.app import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
FROZEN = REPO_ROOT / "frontend" / "packages" / "api-client" / "openapi.json"


def _live() -> dict:
    return create_app().openapi()


def _frozen() -> dict:
    return json.loads(FROZEN.read_text(encoding="utf-8"))


def test_frozen_contract_matches_live_openapi():
    assert FROZEN.exists(), (
        "契约冻结物不存在: 先跑 `cd frontend && npm run gen:api` 生成 "
        "frontend/packages/api-client/openapi.json")
    live, frozen = _live(), _frozen()
    assert live == frozen, (
        "API 契约漂移: 服务端 OpenAPI 与冻结物不一致——改了 ui/contracts.py "
        "或路由后没有重新冻结。重新生成: `cd frontend && npm run gen:api`,"
        "连同 src/schema.d.ts 一起提交")


def test_frozen_contract_is_openapi_3_1():
    frozen = _frozen()
    assert frozen.get("openapi", "").startswith("3.1"), (
        "契约必须是 OpenAPI 3.1(五端类型生成的基线)")
    assert len(frozen.get("paths", {})) >= 20, (
        "契约端点数异常,疑似导出了空/残缺契约")
