"""ui 层测试: API 编排 + runner 子进程桩(SPEC-09 §8)。

原则: 单元测试绝不真跑签到、绝不真改系统调度——store/schedule service
与 runner 的 subprocess 全部打桩;真机验收留给手动步骤。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from auto_signin_kit.core import config as config_m
from auto_signin_kit.ui import runner as runner_mod

# ---------------------------------------------------------------- 桩


class _FakeScheduler:
    """schedule 域 service 需要的最小调度器桩。"""

    platform = "fake"

    def __init__(self, installed: set[str] | None = None) -> None:
        self.installed = installed or set()

    def install(self, spec: Any) -> None:
        self.installed.add(spec.name)

    def uninstall(self, name: str) -> None:
        self.installed.discard(name)

    def status(self, name: str) -> Any:
        from auto_signin_kit.core.ports.scheduler import TaskStatus

        return TaskStatus(name=name, installed=name in self.installed)

    def render(self, spec: Any) -> str:  # pragma: no cover — 本套测试不覆盖 render
        return f"# {spec.name}"


_TODAY = datetime.now().astimezone().date().isoformat()
_YESTERDAY = (datetime.now().astimezone().date() - timedelta(days=1)).isoformat()

# 日期用运行时"今天/昨天": 写死日历日期的桩过不了午夜(真实踩坑)
_DAILY_ROWS = [
    {"day": _TODAY, "domain": "doubao", "result": "CLAIMED",
     "source": "run", "needs_attention": 0, "report": "签到成功", "executed_at": "t"},
    {"day": _TODAY, "domain": "qoder", "result": "NO_AUTH",
     "source": "run", "needs_attention": 1, "report": "登录过期", "executed_at": "t"},
    {"day": _YESTERDAY, "domain": "doubao", "result": "ALREADY",
     "source": "run", "needs_attention": 0, "report": "已签到", "executed_at": "t"},
]


def _patch_history(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """把 store 的两个台账查询换成读桩数据,并记录调用参数。"""
    calls: dict[str, Any] = {}

    def daily_status(days: int = 7, *, domain: str | None = None,
                     source: str | None = None,
                     result: str | None = None,
                     account: str | None = None) -> list[dict]:
        calls["daily"] = {"days": days, "domain": domain, "source": source,
                          "result": result, "account": account}
        rows = [r for r in _DAILY_ROWS if domain is None or r["domain"] == domain]
        return [r for r in rows if result is None or r["result"] == result]

    def events(**kwargs: Any) -> list[dict]:
        calls["events"] = kwargs
        return [{"executed_at": "t", "domain": "doubao", "result": "CLAIMED"}]

    def signed_today() -> dict[str, dict]:
        rows: dict[str, dict] = {}
        for r in _DAILY_ROWS:
            if r["day"] == _TODAY and r["result"] in ("CLAIMED", "ALREADY") \
                    and r["domain"] not in rows:
                rows[r["domain"]] = r
        return rows

    monkeypatch.setattr("auto_signin_kit.ui.api.store.daily_status", daily_status)
    monkeypatch.setattr("auto_signin_kit.ui.api.store.events", events)
    monkeypatch.setattr(
        "auto_signin_kit.ui.api.store.signed_today_by_domain", signed_today)
    return calls


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient: 配置目录隔离到 tmp,调度器/台账全桩。"""
    config_dir = tmp_path / "config"
    (config_dir / "domains").mkdir(parents=True)
    (config_dir / "domains" / "schedule.yaml").write_text(
        "enabled_tasks: []\n", encoding="utf-8")
    monkeypatch.setattr(config_m, "resolve_config_dir", lambda: config_dir)
    from auto_signin_kit.ui.app import create_app

    return TestClient(create_app())


# ---------------------------------------------------------------- 看板/历史


class TestReadRoutes:
    def test_overview_cards_cover_domains_and_week_ratio(self, client, monkeypatch):
        _patch_history(monkeypatch)
        payload = client.get("/api/overview").json()
        domains = {c["domain"] for c in payload["cards"]}
        assert {"doubao", "qoder", "workbuddy", "trae"} <= domains
        doubao = next(c for c in payload["cards"] if c["domain"] == "doubao")
        assert doubao["week_ok"] == 2 and doubao["week_total"] == 2
        assert doubao["today"]["result"] == "CLAIMED"

    def test_overview_ok_ratio_ignores_failures(self, client, monkeypatch):
        _patch_history(monkeypatch)
        qoder = next(c for c in client.get("/api/overview").json()["cards"]
                     if c["domain"] == "qoder")
        assert qoder["week_ok"] == 0 and qoder["week_total"] == 1

    def test_overview_signed_uses_success_row_not_last_row(self, client, monkeypatch):
        """doubao 成功行判"已签";qoder 最后一行 NO_AUTH 只进 today_attention。"""
        _patch_history(monkeypatch)
        cards = {c["domain"]: c for c in client.get("/api/overview").json()["cards"]}
        assert cards["doubao"]["today_signed"] is True
        assert cards["doubao"]["today"]["result"] == "CLAIMED"
        assert cards["qoder"]["today_signed"] is False
        assert cards["qoder"]["today_attention"] is True
        assert cards["qoder"]["today"]["result"] == "NO_AUTH"

    def test_history_passes_days_and_domain(self, client, monkeypatch):
        calls = _patch_history(monkeypatch)
        rows = client.get("/api/history", params={"days": 3, "domain": "doubao"}).json()
        assert calls["daily"] == {"days": 3, "domain": "doubao", "source": None,
                                  "result": None, "account": None}
        assert all(r["domain"] == "doubao" for r in rows)

    def test_history_passes_result_filter(self, client, monkeypatch):
        calls = _patch_history(monkeypatch)
        rows = client.get("/api/history", params={"days": 3, "result": "CLAIMED"}).json()
        assert calls["daily"]["result"] == "CLAIMED"
        assert all(r["result"] == "CLAIMED" for r in rows)

    def test_history_rejects_out_of_range_days(self, client):
        assert client.get("/api/history", params={"days": 0}).status_code == 422
        assert client.get("/api/history", params={"days": 91}).status_code == 422

    def test_events_passes_filters(self, client, monkeypatch):
        calls = _patch_history(monkeypatch)
        client.get("/api/events", params={
            "date": "2026-11-02", "domain": "doubao", "attention_only": True, "limit": 5})
        assert calls["events"] == {
            "date": "2026-11-02", "domain": "doubao", "attention_only": True,
            "result": None, "account": None, "limit": 5}


# ---------------------------------------------------------------- 调度管理


class TestScheduleRoutes:
    def test_status_uses_injected_scheduler(self, client, monkeypatch):
        _patch_history(monkeypatch)
        monkeypatch.setattr("auto_signin_kit.ui.api.get_scheduler",
                            lambda: _FakeScheduler({"DoubaoDailyCheckin"}))
        payload = client.get("/api/schedule").json()
        assert payload["platform"] == "fake"
        by_name = {t["name"]: t["installed"] for t in payload["tasks"]}
        assert by_name["DoubaoDailyCheckin"] is True
        assert by_name["WorkBuddyAutoSignin"] is False

    def test_install_partial_reports_failures(self, client, monkeypatch):
        monkeypatch.setattr("auto_signin_kit.ui.api.get_scheduler",
                            lambda: _FakeScheduler())
        monkeypatch.setattr(
            "auto_signin_kit.ui.api.schedule_service.filter_ready",
            lambda tasks, probe: (tasks, [{"name": "WorkBuddyAutoSignin",
                                           "error": "未检测到"}]))
        payload = client.post("/api/schedule/install", json={}).json()
        assert payload["result"] == "PARTIAL"
        assert payload["failed"] == [{"name": "WorkBuddyAutoSignin", "error": "未检测到"}]
        assert {d["name"] for d in payload["done"]} >= {"DoubaoDailyCheckin"}

    def test_uninstall_idempotent(self, client, monkeypatch):
        monkeypatch.setattr("auto_signin_kit.ui.api.get_scheduler",
                            lambda: _FakeScheduler())
        payload = client.post("/api/schedule/uninstall", json={"domains": ["doubao"]}).json()
        assert payload["result"] == "OK" and payload["done"]

    def test_unknown_domain_404(self, client):
        assert client.post("/api/schedule/install",
                           json={"domains": ["nope"]}).status_code == 404

    def test_multiple_domains_422(self, client):
        assert client.post("/api/schedule/install",
                           json={"domains": ["doubao", "trae"]}).status_code == 422


# ---------------------------------------------------------------- 触发与 job


class TestRunRoutes:
    def test_run_unknown_domain_404(self, client):
        assert client.post("/api/run/nope").status_code == 404

    def test_run_invalid_action_422(self, client):
        assert client.post("/api/run/doubao", params={"action": "install"}).status_code == 422

    def test_run_busy_409(self, client, monkeypatch):
        def busy(domain: str, action: str, account: str | None = None) -> None:
            raise runner_mod.DomainBusyError("doubao auto 已在运行")

        monkeypatch.setattr(runner_mod.runner, "submit", busy)
        assert client.post("/api/run/doubao").status_code == 409

    def test_run_returns_job_and_poll(self, client, monkeypatch):
        from auto_signin_kit.ui.runner import Job

        def fake_submit(domain: str, action: str,
                        account: str | None = None) -> Job:
            job = Job(id="abc123", domain=domain, action=action,
                      argv=("auto-signin-kit", domain, action), started_at="t",
                      status="ok", exit_code=0, output="{}",
                      result={"result": "ALREADY"}, finished_at="t")
            runner_mod.runner._jobs[job.id] = job  # 真实 submit 会登记;桩同样登记才能 poll
            return job

        monkeypatch.setattr(runner_mod.runner, "submit", fake_submit)
        payload = client.post("/api/run/doubao").json()["job"]
        assert payload["id"] == "abc123" and payload["result"]["result"] == "ALREADY"
        polled = client.get("/api/jobs/abc123").json()["job"]
        assert polled["status"] == "ok"

    def test_job_missing_404(self, client):
        assert client.get("/api/jobs/no-such").status_code == 404

    def test_run_with_account_param(self, client, monkeypatch):
        """SPEC-12 P3: /api/run 透传 account 到 runner.submit。"""
        got: dict[str, Any] = {}

        def fake_submit(domain: str, action: str, account: str | None = None):
            got["account"] = account
            from auto_signin_kit.ui.runner import Job

            job = Job(id="acc1", domain=domain, action=action,
                      argv=("auto-signin-kit", domain, action), started_at="t",
                      account=account, status="ok")  # 立即置完成,避免占用互斥位
            runner_mod.runner._jobs[job.id] = job
            return job

        monkeypatch.setattr(runner_mod.runner, "submit", fake_submit)
        client.post("/api/run/workbuddy", params={"account": "main"})
        assert got["account"] == "main"


# ---------------------------------------------------------------- runner 本体


class TestRunner:
    def test_last_json_takes_final_parseable_line(self):
        text = 'not json\n{"a": 1}\n{"a": 2, "b": "x"}\nalso not'
        assert runner_mod._last_json(text) == {"a": 2, "b": "x"}
        assert runner_mod._last_json("nothing here") is None

    def test_submit_runs_cli_and_parses_result(self, monkeypatch):
        captured: dict[str, Any] = {}

        def fake_run(argv: Any, **kwargs: Any) -> Any:
            captured["argv"] = tuple(argv)
            return subprocess_result(stdout='{"result": "ALREADY", "report": "已签到"}\n',
                                     returncode=0)

        monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
        job = runner_mod.runner.submit("doubao", "auto")
        _wait_finished(job.id)
        done = runner_mod.runner.get(job.id)
        assert done is not None and done.status == "ok" and done.exit_code == 0
        assert done.result == {"result": "ALREADY", "report": "已签到"}
        assert captured["argv"] == ("auto-signin-kit", "doubao", "auto")

    def test_submit_rejects_unlisted_action(self):
        with pytest.raises(ValueError):
            runner_mod.runner.submit("doubao", "install")

    def test_same_domain_action_busy(self, monkeypatch):
        def fake_run(argv: Any, **kwargs: Any) -> Any:
            time.sleep(0.3)  # 拉长第一次运行窗口,让第二次 submit 撞上 busy
            return subprocess_result(stdout="{}", returncode=0)

        monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
        first = runner_mod.runner.submit("doubao", "doctor")
        with pytest.raises(runner_mod.DomainBusyError):
            runner_mod.runner.submit("doubao", "doctor")
        _wait_finished(first.id)

    def test_subprocess_failure_marks_failed(self, monkeypatch):
        monkeypatch.setattr(runner_mod.subprocess, "run",
                            lambda argv, **kw: subprocess_result(stdout="", returncode=2))
        job = runner_mod.runner.submit("doubao", "doctor")
        _wait_finished(job.id)
        done = runner_mod.runner.get(job.id)
        assert done is not None and done.status == "failed" and done.exit_code == 2

    def test_submit_with_account_carries_flag(self, monkeypatch):
        """SPEC-12 P3: 指定账户手动触发 → 子进程带 --account。"""
        captured: dict[str, Any] = {}

        def fake_run(argv: Any, **kwargs: Any) -> Any:
            captured["argv"] = tuple(argv)
            return subprocess_result(stdout='{"result": "OK", "report": "ok"}\n',
                                     returncode=0)

        monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
        job = runner_mod.runner.submit("workbuddy", "auto", account="main")
        _wait_finished(job.id)
        assert captured["argv"] == ("auto-signin-kit", "workbuddy", "auto",
                                    "--account", "main")
        assert runner_mod.runner.get(job.id).account == "main"

    def test_different_accounts_not_busy(self, monkeypatch):
        """不同账户各自独立凭据与台账行,同域同动作可并发;同账户才互斥。"""
        monkeypatch.setattr(runner_mod.subprocess, "run",
                            lambda argv, **kw: (time.sleep(0.2),
                                                subprocess_result(stdout="{}", returncode=0))[1])
        first = runner_mod.runner.submit("doubao", "doctor", account="a")
        second = runner_mod.runner.submit("doubao", "doctor", account="b")  # 不抛
        with pytest.raises(runner_mod.DomainBusyError):
            runner_mod.runner.submit("doubao", "doctor", account="a")
        _wait_finished(first.id)
        _wait_finished(second.id)


def subprocess_result(*, stdout: str, returncode: int) -> Any:
    """最小 subprocess.CompletedProcess 桩(runner 只用 stdout/returncode)。"""
    from types import SimpleNamespace

    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


def _wait_finished(job_id: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runner_mod.runner.get(job_id)
        if job is not None and job.status != "running":
            return
        time.sleep(0.02)
    raise AssertionError("job 未在时限内结束")


# ---------------------------------------------------------------- 账户视图(SPEC-12 P3)


class TestAccountsRoutes:
    """账户视图: 登记账户 × 逐账户运行统计合并;启停只改登记态。"""

    def test_accounts_list_merges_registry_and_stats(self, client):
        import json

        from auto_signin_kit.core import store

        store.record_payload(json.dumps({
            "ts": "2026-11-05T09:00:00+08:00", "level": "INFO", "domain": "trae",
            "action": "auto", "result": "CLAIMED", "needs_attention": False,
            "report": "ok", "credits": 30, "account": "main"}, ensure_ascii=False))
        out = client.get("/api/accounts").json()
        row = next(r for r in out["accounts"]
                   if r["domain"] == "trae" and r["name"] == "main")
        assert row["enabled"] is True and row["streak"] == 1
        assert row["today_credits"] == 30

    def test_account_toggle_roundtrip_and_404(self, client):
        import json

        from auto_signin_kit.core import store

        store.record_payload(json.dumps({
            "ts": "2026-11-05T09:00:00+08:00", "level": "INFO", "domain": "trae",
            "action": "auto", "result": "CLAIMED", "needs_attention": False,
            "report": "ok"}, ensure_ascii=False))
        ok = client.post("/api/accounts/enabled",
                         json={"domain": "trae", "name": "default", "enabled": False})
        assert ok.status_code == 200
        row = next(r for r in client.get("/api/accounts").json()["accounts"]
                   if r["domain"] == "trae")
        assert row["enabled"] is False
        missing = client.post("/api/accounts/enabled",
                              json={"domain": "trae", "name": "ghost", "enabled": True})
        assert missing.status_code == 404
