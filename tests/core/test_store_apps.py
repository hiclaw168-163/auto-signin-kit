"""ask_apps 应用主档测试: 惰性建行 / 部分更新 / enabled 默认语义 / 老库迁移。

台账隔离由 tests/conftest.py 的 autouse fixture 提供(AUTO_SIGNIN_KIT_DATA_DIR
指到 tmp),这里连真实 schema 真实库,不桩 SQL。
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from auto_signin_kit.core import store
from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.store.connection import db_path


class TestAppState:
    def test_missing_app_defaults_enabled(self):
        """从未操作过的应用: 无行,app_enabled 视为启用。"""
        assert store.app_state("trae") is None
        assert store.app_enabled("trae") is True

    def test_ensure_app_is_idempotent(self):
        store.ensure_app("trae")
        store.ensure_app("trae")
        with store.connect() as conn:
            rows = conn.execute("SELECT * FROM ask_apps WHERE app_code='trae'").fetchall()
        assert len(rows) == 1  # UNIQUE(app_code, region) 挡重复,INSERT OR IGNORE 静默

    def test_ensure_app_records_source_and_profiles(self):
        """来源与档案默认值在建行时落库: manual 标记用户手动新建。"""
        store.ensure_app("demoapp", "cn", app_name="Demo", app_desc="说明",
                         source="manual")
        row = store.app_state("demoapp")
        assert row["source"] == "manual"
        assert row["app_name"] == "Demo" and row["app_desc"] == "说明"

    def test_ensure_app_profiles_fill_if_empty_only(self):
        """档案默认值只补空: 空串/NULL 都算未填,用户填过非空值不被同步翻掉。"""
        store.ensure_app("demoapp", "cn")  # 先建空行(无档案默认值)
        store.ensure_app("demoapp", "cn", app_name="Demo", app_desc="D")
        row = store.app_state("demoapp")
        assert row["app_name"] == "Demo" and row["app_desc"] == "D"
        # 用户覆盖 → 再同步也不覆盖
        store.set_app_state("demoapp", app_name="我的名字")
        store.ensure_app("demoapp", "cn", app_name="Demo")
        assert store.app_state("demoapp")["app_name"] == "我的名字"
        # 历史空串行(UI 显式清空过的)也按未填补齐
        store.set_app_state("demoapp", app_name="")  # 显式清空
        store.ensure_app("demoapp", "cn", app_name="Demo")
        assert store.app_state("demoapp")["app_name"] == "Demo"

    def test_ensure_app_source_never_flipped(self):
        """来源只在建行时落值: manual 行再被自动同步扫到仍是 manual。"""
        store.ensure_app("demoapp", "cn", source="manual")
        store.ensure_app("demoapp", "cn", source="auto")
        assert store.app_state("demoapp")["source"] == "manual"

    def test_ensure_app_default_source_is_auto(self):
        store.ensure_app("demoapp")
        assert store.app_state("demoapp")["source"] == "auto"

    def test_ensure_app_region_variants(self):
        """region 就地纠正: 同步探测结论比历史行可信,行迁移到新 region。

        当前契约每应用一行(真·双版本共存走独立域,见 ask_apps.sql 头注释):
        已有 cn 行、探测结论 intl 时,行迁到 intl 而不是留着错值或造第二行。
        """
        store.ensure_app("qoder", "cn")
        store.ensure_app("qoder", "intl")
        with store.connect() as conn:
            rows = conn.execute(
                "SELECT region FROM ask_apps WHERE app_code='qoder'").fetchall()
        assert [r["region"] for r in rows] == ["intl"]
        # 再同步回 cn 也同样收敛到一行
        store.ensure_app("qoder", "cn")
        with store.connect() as conn:
            rows = conn.execute(
                "SELECT region FROM ask_apps WHERE app_code='qoder'").fetchall()
        assert [r["region"] for r in rows] == ["cn"]

    def test_set_state_partial_update(self):
        store.ensure_app("trae")
        row = store.set_app_state("trae", enabled=False)
        assert row["enabled"] == 0
        # 部分更新: 只改 app_desc 不动 enabled
        row = store.set_app_state("trae", app_desc="测试备注")
        assert row["enabled"] == 0
        assert row["app_desc"] == "测试备注"

    def test_set_state_creates_row_lazily(self):
        """不 ensure 直接 set 也能工作(惰性建行)。"""
        row = store.set_app_state("qoder", enabled=True, app_name="Qoder CN 覆盖名")
        assert row["app_code"] == "qoder"
        assert row["region"] == "cn"
        assert row["enabled"] == 1
        assert row["app_name"] == "Qoder CN 覆盖名"
        assert row["created_at"] is not None and row["updated_at"] is not None

    def test_audit_timestamps_on_update(self):
        store.ensure_app("trae")
        first = store.app_state("trae")
        second = store.set_app_state("trae", app_desc="x")
        assert second["updated_at"] >= first["updated_at"]

    def test_db_error_wrapped(self):
        with patch("auto_signin_kit.core.store.apps.connect",
                   side_effect=sqlite3.OperationalError("boom")), pytest.raises(AutoSignInKitError):
            store.set_app_state("trae", enabled=False)


class TestLegacyMigration:
    def _legacy_ask_apps(self, db: sqlite3.Connection) -> None:
        """2026-11-05 首版 ask_apps(domain/display/note,无 region)。"""
        db.executescript(
            """
            CREATE TABLE ask_apps (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                domain      TEXT    NOT NULL,
                enabled     INTEGER NOT NULL DEFAULT 1,
                display     TEXT,
                note        TEXT,
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL,
                UNIQUE(domain)
            );
            INSERT INTO ask_apps (domain, enabled, display, note,
                                  created_at, updated_at)
            VALUES ('trae', 0, 'Trae 覆盖名', '旧备注', '2026-11-05 10:00:00',
                    '2026-11-05 10:00:00');
            """
        )

    def test_legacy_columns_renamed_and_region_backfilled(self, tmp_path, monkeypatch):
        """老库连上即迁移: 三列改名 + region 补列回填 + 列序重建,数据保留。"""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setenv("AUTO_SIGNIN_KIT_DATA_DIR", str(data_dir))
        db = sqlite3.connect(db_path())
        db.row_factory = sqlite3.Row
        try:
            self._legacy_ask_apps(db)
            db.commit()
        finally:
            db.close()
        # 重连触发迁移
        with store.connect() as conn:
            row = conn.execute("SELECT * FROM ask_apps").fetchone()
            assert row["app_code"] == "trae_cn"    # domain → app_code(迁移改名 trae→trae_cn)
            assert row["app_name"] == "Trae 覆盖名"  # display → app_name
            assert row["app_desc"] == "旧备注"        # note → app_desc
            assert row["region"] == "cn"           # 补列回填
            assert row["enabled"] == 0             # 开关值原样保留
            assert row["source"] == "auto"         # 补列回填(来源列引入前的行)
            # kind: 补列时 NULL;trae_cn 是存量应用,类别迁移时按凭据来源
            # 口径一次性规整为 client(凭据来自 Trae 桌面端本地存储)
            assert row["kind"] == "client"
            assert row["credits_refresh_time"] == "00:00"  # 积分刷新: 一次性回填默认值
            # version: 2026-11-06 补列(NULL——老库无探测数据,等下次同步落值)
            assert row["version"] is None
            cols = [r["name"] for r in
                    conn.execute("PRAGMA table_info(ask_apps)")]
        assert cols == ["id", "app_code", "app_name", "app_desc", "kind", "version",
                        "region", "credits_refresh_time", "enabled", "source",
                        "created_at", "updated_at"]
