"""workbuddy 域 CLI 冒烟: 无凭据路径与未知命令,不联网。"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auto_signin_kit.domains.workbuddy import daily, rewards, service
from auto_signin_kit.domains.workbuddy.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """凭据路径强制指向不存在文件 + 日志隔离;不依赖真实客户端安装,也不落盘到仓库。"""
    monkeypatch.setenv("WORKBUDDY_AUTH_FILE", str(tmp_path / "no-such.info"))
    monkeypatch.delenv("WORKBUDDY_EXE", raising=False)
    monkeypatch.setattr(service, "log_path", lambda: Path(tmp_path) / "signin.log")
    monkeypatch.setattr(service.Budget, "remaining", lambda self: 300.0)


def test_no_auth_reports_no_auth_with_exit_2(tmp_path, monkeypatch, capsys):
    """凭据缺失必须给 NO_AUTH + looked_in,退出码 2——这是排错的起点。"""
    result = runner.invoke(app, ["auto"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["result"] == "NO_AUTH"
    assert payload["needs_attention"] is True
    assert isinstance(payload["looked_in"], list)


def test_doctor_without_credentials_gives_no_auth(tmp_path, monkeypatch):
    """doctor 没有 doctor 结果;没凭据同样 NO_AUTH/退出码 2。"""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["result"] == "NO_AUTH"


class TestTodayActivity:
    """活跃天数只认对话: score>0 才算今天活跃过,领积分不计。"""

    def _body(self, score):
        return {"today": {"date": "2026-10-21"},
                "cells": [{"date": "2026-10-21", "score": score}]}

    def test_score_positive_means_active(self):
        out = rewards.today_activity(self._body(3))
        assert out["result"] == "ACTIVE"
        assert out["needs_attention"] is False

    def test_zero_score_warns_chat_is_needed(self):
        """领了积分但没对话: score 仍为 0,必须提醒,否则活跃天数静默中断。"""
        out = rewards.today_activity(self._body(0))
        assert out["result"] == "NO_ACTIVITY"
        assert out["needs_attention"] is True

    def test_missing_today_is_no_data(self):
        out = rewards.today_activity({"today": {"date": "2026-10-21"}, "cells": []})
        assert out["result"] == "NO_DATA"

    def test_malformed_calendar_raises_instead_of_guessing(self):
        """日历结构异常要显式报错,不能推测为"未活跃"去误导用户。"""
        with pytest.raises(ValueError):
            rewards.today_activity({"today": {"date": "bad-date"}, "cells": []})


class _StubNet:
    """daily 步骤只读 budget_remaining;业务函数已被替身拦截,无需真 Net。"""

    budget_remaining = 300.0


class TestDailyPipeline:
    """daily.run_daily 流水线: 签到门控中止后续步骤,聚合回报契约不变。"""

    def _run(self, monkeypatch, checkin_result, growth_result=None):
        """替身注入签到/成长结果,返回 (code, out, quiet, 成长调用次数)。"""
        calls = {"growth": 0}

        def fake_run_auto(net):
            return checkin_result

        def fake_run_growth(net, budget):
            calls["growth"] += 1
            return growth_result

        monkeypatch.setattr(daily.checkin, "run_auto", fake_run_auto)
        monkeypatch.setattr(daily, "run_growth", fake_run_growth)
        code, out, quiet = daily.run_daily(_StubNet())
        return code, out, quiet, calls

    def test_network_gate_skips_growth(self, monkeypatch):
        """网络不可达必须中止流水线: 成长中心一次都不跑,签到事实照常汇报。"""
        code, out, quiet, calls = self._run(
            monkeypatch, (1, {"result": "NETWORK", "report": "网络不可达"})
        )
        assert calls["growth"] == 0
        assert out["growth_result"] == "NETWORK"
        assert "跳过" in out["growth"]
        assert (code, quiet, out["needs_attention"]) == (1, False, True)

    def test_auth_gate_skips_growth(self, monkeypatch):
        """认证拒绝同样中止流水线,且 growth_result 带上门控原因。"""
        code, out, _, calls = self._run(
            monkeypatch, (1, {"result": "AUTH_REJECTED", "report": "401"})
        )
        assert calls["growth"] == 0
        assert out["growth_result"] == "AUTH_REJECTED"
        assert code == 1

    def test_already_and_idle_is_quiet(self, monkeypatch):
        """已签 + 成长中心无事可做 = 空跑,退出码 0,报告不被追加。"""
        code, out, quiet, _ = self._run(
            monkeypatch,
            (0, {"result": "ALREADY", "report": "今日已签过"}),
            (0, {"result": "IDLE", "report": "无事可做", "idle": True}),
        )
        assert (code, quiet, out["needs_attention"]) == (0, True, False)
        assert out["report"] == "今日已签过"
        assert out["growth_result"] == "IDLE"

    def test_growth_failure_keeps_checkin_fact(self, monkeypatch):
        """成长失败不吞掉签到成功的事实: 报告合并、退出码抬高、置位提醒。"""
        code, out, quiet, _ = self._run(
            monkeypatch,
            (0, {"result": "CLAIMED", "report": "成功领取 5 积分"}),
            (1, {"result": "ERROR", "report": "成长中心异常"}),
        )
        assert out["report"] == "成功领取 5 积分；成长中心异常"
        assert (code, quiet, out["needs_attention"]) == (1, False, True)

    def test_growth_credits_merge_report_without_failure(self, monkeypatch):
        """成长有积分入账也要进报告,但不抬高退出码。"""
        code, out, _, _ = self._run(
            monkeypatch,
            (0, {"result": "CLAIMED", "report": "成功领取 5 积分"}),
            (0, {"result": "GROWN", "report": "旅行归来", "credits_gained": 3}),
        )
        assert out["report"] == "成功领取 5 积分；旅行归来"
        assert (code, out["needs_attention"]) == (0, False)
