#!/usr/bin/env python
"""导出 API 契约: create_app() → OpenAPI 3.1 JSON → stdout 或目标路径。

契约层(P0,PROPOSAL-01)的"单一事实源"出口。各端消费方式:
- TS 类型: ``npm run gen:api``(frontend/packages/api-client,openapi-typescript)
- 冻结门禁: tests/test_contract_gate.py 重导出比对 ``frontend/packages/api-client/openapi.json``

用法::

    uv run python scripts/export_openapi.py                  # 打印到 stdout
    uv run python scripts/export_openapi.py <目标路径.json>   # 写文件

注意: 不起服务器、不连真实台账——纯 app.openapi() 内省,任何机器可跑。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path


def build() -> dict:
    """构建 FastAPI app 并内省出 OpenAPI 文档(dict)。"""
    from auto_signin_kit.ui.app import create_app

    return create_app().openapi()


def main(argv: Sequence[str] | None = None) -> int:
    """导出入口: 有参数写文件并打摘要到 stderr,无参数打印契约到 stdout。"""
    args = list(sys.argv[1:] if argv is None else argv)
    doc = build()
    text = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if args:
        path = Path(args[0])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        sys.stderr.write(f"契约已导出: {path} ({len(doc.get('paths', {}))} 个端点)\n")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
