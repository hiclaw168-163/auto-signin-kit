"""init 一键初始化的单元测试(全部注入,不碰真实调度/守护进程)。"""

from __future__ import annotations

from typing import Any

import pytest

from auto_signin_kit.init import service


def _fake_confirm(answers: dict[str, bool]) -> Any:
    def confirm(message: str, default: bool) -> bool:
        return answers.get(message, default)

    return confirm


class TestBusinessDomains:
    def test_lists_all_registered_schedule_meta_domains(self) -> None:
        names = {d["domain"] for d in service.business_domains()}
        assert {"workbuddy", "qoder", "trae", "doubao"} <= names
        for d in service.business_domains():
            assert d["display"], "展示名缺失"


class TestRunInit:
    def test_non_interactive_installs_and_reports_ready(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        self._stub_env(monkeypatch, tmp_path)
        result = service.run_init(interactive=False, start_ui=False, confirm=None)
        assert result["exit"] == 0
        assert not result["todo"]
        assert result["installed"], "非交互模式应默认全启用并注册任务"
        assert result["ui"] == "skipped(--no-ui)"

    def test_interactive_can_disable_domain(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        self._stub_env(monkeypatch, tmp_path)
        # qoder 选"否",其余默认"是"
        result = service.run_init(
            interactive=True,
            start_ui=False,
            confirm=_fake_confirm({"启用 Qoder CN(qoder)的每日自动任务?": False}),
        )
        assert "qoder" in result["disabled"]
        assert "qoder" not in result["enabled"]
        assert not any(t["name"].startswith("Qoder") for t in result["installed"])

    def test_unready_client_becomes_todo_not_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        self._stub_env(monkeypatch, tmp_path, unready={"doubao"})
        result = service.run_init(interactive=False, start_ui=False, confirm=None)
        assert any(i["domain"] == "doubao" for i in result["not_ready"])
        assert result["todo"], "未就绪域必须出现在待办清单里"
        assert result["exit"] == 1
        # 其余域不受连累,照常注册
        assert any(t["name"].startswith("WorkBuddy") for t in result["installed"])

    def test_env_hard_fail_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        self._stub_env(monkeypatch, tmp_path)
        monkeypatch.setattr(
            service, "env_checks",
            lambda: [{"name": "业务域注册表", "ok": False, "block": True, "detail": "空"}],
        )
        result = service.run_init(interactive=False, start_ui=False, confirm=None)
        assert result["exit"] == 2
        assert not result["installed"]

    # ---- 桩设施 -------------------------------------------------------

    def _stub_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
        unready: set[str] | None = None,
    ) -> None:
        """隔离三件事: 配置加载/调度注册/守护进程,全部换内存桩。"""
        unready = unready or set()

        from auto_signin_kit.domains.schedule import service as sched

        # 台账写入换桩: 单测绝不污染真实 ledger
        monkeypatch.setattr(service, "emit_result", lambda *a, **k: None)
        monkeypatch.setattr(service, "_get_scheduler", lambda: _RecordingScheduler())
        monkeypatch.setattr(
            service, "_default_probe",
            lambda domain: ("%s 客户端未检测到" % domain) if domain in unready else None,
        )
        monkeypatch.setattr(sched, "_default_probe", service._default_probe)
        monkeypatch.setattr(
            service, "env_checks",
            lambda: [
                {"name": "业务域注册表", "ok": True, "block": True,
                 "detail": "发现 4 个域"},
                {"name": "数据目录可写", "ok": True, "block": True,
                 "detail": str(tmp_path)},
                {"name": "调度配置", "ok": True, "block": True, "detail": "加载成功"},
            ],
        )
        monkeypatch.setattr(
            service, "load_domain_config_model",
            lambda *a, **k: type("C", (), {"enabled_tasks": None, "tasks": {}})(),
        )


class _RecordingScheduler:
    """记录 install 调用的桩调度器。"""

    def __init__(self) -> None:
        self.installed: list[str] = []

    def install(self, spec: Any) -> None:
        self.installed.append(spec.name)

    def uninstall(self, spec: Any) -> None:
        pass

    def status(self, spec: Any) -> bool:
        return False

    def render(self, spec: Any) -> str:
        return ""
