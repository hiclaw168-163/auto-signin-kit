"""应用配置编辑 API 测试: GET 字段视图 / PUT 部分更新(全离线,tmp 配置目录)。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from auto_signin_kit.ui import config_api as config_api_m

_DOUBAO_YAML = """\
base_url: https://www.doubao.com
campaign_code: pc_incentive_task_mvp_v3
budget_seconds: 240
"""


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient: 配置目录隔离到 tmp(config_api 直接引用了 resolve_config_dir,
    必须连同它一起 patch,只 patch core 模块不生效)。"""
    config_dir = tmp_path / "config"
    (config_dir / "domains").mkdir(parents=True)
    (config_dir / "domains" / "doubao.yaml").write_text(_DOUBAO_YAML,
                                                        encoding="utf-8")
    monkeypatch.setattr(config_api_m, "resolve_config_dir", lambda: config_dir)
    from auto_signin_kit.ui.app import create_app

    return TestClient(create_app()), config_dir


def _yaml(config_dir, name="doubao") -> str:
    return (config_dir / "domains" / f"{name}.yaml").read_text(encoding="utf-8")


class TestGetAppConfig:
    def test_schema_cls_prefers_class_defined_in_module(self):
        """回归: _schema_cls 不能误取 import 进来的基类 BudgetedConfig——
        否则具体域字段(base_url 等)全被当 extra 拒绝。"""
        from auto_signin_kit.domains.doubao.schema import DoubaoDomainConfig
        from auto_signin_kit.ui.config_api import _schema_cls

        assert _schema_cls("doubao") is DoubaoDomainConfig

    def test_returns_parsed_fields(self, client):
        _, _config_dir = client
        out: dict[str, Any] = client[0].get("/api/apps/doubao/config").json()
        assert out["domain"] == "doubao"
        assert out["fields"]["campaign_code"] == "pc_incentive_task_mvp_v3"
        assert out["fields"]["budget_seconds"] == 240

    def test_unknown_domain_404(self, client):
        assert client[0].get("/api/apps/nope/config").status_code == 404

    def test_missing_yaml_404(self, client, tmp_path):
        got, config_dir = client
        (config_dir / "domains" / "doubao.yaml").unlink()
        assert got.get("/api/apps/doubao/config").status_code == 404


class TestUpdateApp:
    def test_partial_update_merges_and_writes(self, client):
        got, config_dir = client
        resp = got.put("/api/apps/doubao",
                       json={"updates": {"campaign_code": "pc_next_season"}})
        assert resp.status_code == 200
        data = _yaml(config_dir)
        assert "campaign_code: pc_next_season" in data
        assert "base_url: https://www.doubao.com" in data  # 未改字段不丢

    def test_invalid_value_422_with_loc(self, client):
        got, _config_dir = client
        resp = got.put("/api/apps/doubao",
                       json={"updates": {"budget_seconds": 0}})
        assert resp.status_code == 422
        assert "budget_seconds" in str(resp.json()["detail"])

    def test_unknown_field_rejected_by_extra_forbid(self, client):
        got, config_dir = client
        resp = got.put("/api/apps/doubao", json={"updates": {"bogus": 1}})
        assert resp.status_code == 422
        assert _yaml(config_dir) == _DOUBAO_YAML  # 校验失败不落盘

    def test_empty_updates_422(self, client):
        got, config_dir = client
        assert got.put("/api/apps/doubao", json={"updates": {}}).status_code == 422
        assert _yaml(config_dir) == _DOUBAO_YAML

    def test_unknown_domain_404(self, client):
        got, _ = client
        assert got.put("/api/apps/nope",
                       json={"updates": {"base_url": "https://x"}}).status_code == 404

    def test_written_yaml_roundtrips_through_domain_schema(self, client):
        """写回的 YAML 必须能被域 schema 校验读回(与 CLI 同一条链路闭环)。"""
        import yaml as yaml_m

        from auto_signin_kit.domains.doubao.schema import DoubaoDomainConfig

        got, config_dir = client
        assert got.put("/api/apps/doubao", json={
            "updates": {"budget_seconds": 120}}).status_code == 200
        model = DoubaoDomainConfig.model_validate(
            yaml_m.safe_load(_yaml(config_dir)))
        assert model.budget_seconds == 120

    def test_update_schedule_task_times(self, client):
        """任务时间编辑闭环: PUT /apps/schedule 深合并写 TaskOverride.times。"""
        got, config_dir = client
        (config_dir / "domains" / "schedule.yaml").write_text(
            "enabled_tasks: []\n", encoding="utf-8")
        resp = got.put("/api/apps/schedule", json={
            "updates": {"tasks": {"DoubaoDailyCheckin":
                                  {"times": ["12:00", "09:05"]}}}})
        assert resp.status_code == 200
        data = _yaml(config_dir, "schedule")
        assert "DoubaoDailyCheckin" in data
        assert "12:00" in data and "09:05" in data

    def test_update_schedule_invalid_time_422(self, client):
        got, config_dir = client
        (config_dir / "domains" / "schedule.yaml").write_text(
            "enabled_tasks: []\n", encoding="utf-8")
        resp = got.put("/api/apps/schedule", json={
            "updates": {"tasks": {"DoubaoDailyCheckin": {"times": ["25:00"]}}}})
        assert resp.status_code == 422
        assert _yaml(config_dir, "schedule") == "enabled_tasks: []\n"  # 不落盘
