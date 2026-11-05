"""ask_apps 应用管理态测试: 惰性建行 / 部分更新 / enabled 默认语义。

台账隔离由 tests/conftest.py 的 autouse fixture 提供(AUTO_SIGNIN_KIT_DATA_DIR
指到 tmp),这里连真实 schema 真实库,不桩 SQL。
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from auto_signin_kit.core import store
from auto_signin_kit.core.errors import AutoSignInKitError


class TestAppState:
    def test_missing_app_defaults_enabled(self):
        """从未操作过的应用: 无行,app_enabled 视为启用。"""
        assert store.app_state("trae") is None
        assert store.app_enabled("trae") is True

    def test_ensure_app_is_idempotent(self):
        store.ensure_app("trae")
        store.ensure_app("trae")
        with store.connect() as conn:
            rows = conn.execute("SELECT * FROM ask_apps WHERE domain='trae'").fetchall()
        assert len(rows) == 1  # UNIQUE(domain) 挡重复,INSERT OR IGNORE 静默

    def test_set_state_partial_update(self):
        store.ensure_app("trae")
        row = store.set_app_state("trae", enabled=False)
        assert row["enabled"] == 0
        # 部分更新: 只改 note 不动 enabled
        row = store.set_app_state("trae", note="测试备注")
        assert row["enabled"] == 0
        assert row["note"] == "测试备注"

    def test_set_state_creates_row_lazily(self):
        """不 ensure 直接 set 也能工作(惰性建行)。"""
        row = store.set_app_state("qoder", enabled=True, display="Qoder CN 覆盖名")
        assert row["domain"] == "qoder"
        assert row["enabled"] == 1
        assert row["display"] == "Qoder CN 覆盖名"
        assert row["created_at"] is not None and row["updated_at"] is not None

    def test_audit_timestamps_on_update(self):
        store.ensure_app("trae")
        first = store.app_state("trae")
        second = store.set_app_state("trae", note="x")
        assert second["updated_at"] >= first["updated_at"]

    def test_db_error_wrapped(self):
        with patch("auto_signin_kit.core.store.apps.connect",
                   side_effect=sqlite3.OperationalError("boom")), pytest.raises(AutoSignInKitError):
            store.set_app_state("trae", enabled=False)
