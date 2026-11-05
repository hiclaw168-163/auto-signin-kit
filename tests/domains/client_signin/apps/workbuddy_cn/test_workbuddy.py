"""workbuddy 域 CLI 冒烟: 无凭据路径与未知命令,不联网。"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auto_signin_kit.domains.client_signin.apps.workbuddy_cn import chat, daily, rewards, service
from auto_signin_kit.domains.client_signin.apps.workbuddy_cn.cli import app

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
        """替身注入签到/成长结果,返回 (code, out, 成长调用次数)。"""
        calls = {"growth": 0}

        def fake_run_auto(net):
            return checkin_result

        def fake_run_growth(net, budget, taskbot=False):
            calls["growth"] += 1
            return growth_result

        monkeypatch.setattr(daily.checkin, "run_auto", fake_run_auto)
        monkeypatch.setattr(daily, "run_growth", fake_run_growth)
        code, out = daily.run_daily(_StubNet())
        return code, out, calls

    def test_network_gate_skips_growth(self, monkeypatch):
        """网络不可达必须中止流水线: 成长中心一次都不跑,签到事实照常汇报。"""
        code, out, calls = self._run(
            monkeypatch, (1, {"result": "NETWORK", "report": "网络不可达"})
        )
        assert calls["growth"] == 0
        assert out["growth_result"] == "NETWORK"
        assert "跳过" in out["growth"]
        assert (code, out["needs_attention"]) == (1, True)

    def test_auth_gate_skips_growth(self, monkeypatch):
        """认证拒绝同样中止流水线,且 growth_result 带上门控原因。"""
        code, out, calls = self._run(
            monkeypatch, (1, {"result": "AUTH_REJECTED", "report": "401"})
        )
        assert calls["growth"] == 0
        assert out["growth_result"] == "AUTH_REJECTED"
        assert code == 1

    def test_already_and_idle_keeps_clean_report(self, monkeypatch):
        """已签 + 成长中心无事可做: 退出码 0,报告不被追加(每轮必写由 emit 层保证)。"""
        code, out, _ = self._run(
            monkeypatch,
            (0, {"result": "ALREADY", "report": "今日已签过"}),
            (0, {"result": "IDLE", "report": "无事可做", "idle": True}),
        )
        assert (code, out["needs_attention"]) == (0, False)
        assert out["report"] == "今日已签过"
        assert out["growth_result"] == "IDLE"

    def test_growth_failure_keeps_checkin_fact(self, monkeypatch):
        """成长失败不吞掉签到成功的事实: 报告合并、退出码抬高、置位提醒。"""
        code, out, _ = self._run(
            monkeypatch,
            (0, {"result": "CLAIMED", "report": "成功领取 5 积分"}),
            (1, {"result": "ERROR", "report": "成长中心异常"}),
        )
        assert out["report"] == "成功领取 5 积分；成长中心异常"
        assert (code, out["needs_attention"]) == (1, True)

    def test_growth_credits_merge_report_without_failure(self, monkeypatch):
        """成长有积分入账也要进报告,但不抬高退出码。"""
        code, out, _ = self._run(
            monkeypatch,
            (0, {"result": "CLAIMED", "report": "成功领取 5 积分"}),
            (0, {"result": "GROWN", "report": "旅行归来", "credits_gained": 3}),
        )
        assert out["report"] == "成功领取 5 积分；旅行归来"
        assert (code, out["needs_attention"]) == (0, False)

    def test_credits_snapshot_merges_checkin_and_growth(self, monkeypatch):
        """2026-11-05 补账口径: out["credits"] = 签到所得 + 成长所得——
        台账 _extract_credits 只认方言字段,此前成长积分(旅行礼物/任务奖)
        从未进过积分台账,管理台 workbuddy 今日积分一直少算。"""
        _code, out, _ = self._run(
            monkeypatch,
            (0, {"result": "CLAIMED", "report": "成功领取 5 积分", "credit": 5}),
            (0, {"result": "GROWN", "report": "旅行归来 +3", "credits_gained": 3}),
        )
        assert out["credits"] == 8  # 5(签到) + 3(旅行礼物)

    def test_credits_snapshot_with_today_credit_dialect(self, monkeypatch):
        """签到走 ALREADY 分支时所得在 today_credit 字段,同样要并进去。"""
        _code, out, _ = self._run(
            monkeypatch,
            (0, {"result": "ALREADY", "report": "今日已签过", "today_credit": 5}),
            (0, {"result": "GROWN", "report": "旅行归来 +3", "credits_gained": 3}),
        )
        assert out["credits"] == 8


class TestParseSseText:
    def test_aggregates_deltas_and_stops_at_done(self):
        raw = ('data: {"choices":[{"delta":{"content":"好"}}]}\n'
               'data: {"choices":[{"delta":{"content":"的"}}]}\n'
               'data: [DONE]\n')
        assert chat.parse_sse_text(raw) == "好的"

    def test_tolerates_garbage_lines(self):
        raw = "event: ping\ndata: not-json\ndata: {}\ndata: [DONE]\n"
        assert chat.parse_sse_text(raw) == ""


class _FakeNet:
    """duck-typed Net: get/post 按序喂响应。"""

    def __init__(self, gets=(), posts=()):
        self._gets = list(gets)
        self._posts = list(posts)
        self.get_calls = 0
        self.post_calls = 0

    def budget_remaining(self):
        return 60.0

    def get(self, suffix):
        self.get_calls += 1
        return self._gets[min(self.get_calls - 1, len(self._gets) - 1)]

    def post(self, suffix, payload=None, retry=False):
        self.post_calls += 1
        return self._posts[min(self.post_calls - 1, len(self._posts) - 1)]


def _heatmap(score):
    return {"today": {"date": "2026-10-22"},
            "cells": [{"date": "2026-10-22", "score": score}]}


_SSE_OK = (200, {"raw": 'data: {"choices":[{"delta":{"content":"好的"}}]}\ndata: [DONE]\n'})


class TestRunActiveChain:
    """活跃保底链: 无记录 → 补对话 → 复查记分。"""

    def test_inactive_then_chat_then_active(self):
        net = _FakeNet(
            gets=[(200, _heatmap(0)), (200, _heatmap(5))],
            posts=[_SSE_OK],
        )
        out = rewards.run_active(net)
        assert out["result"] == "ACTIVE"
        assert out["chatted"] is True
        assert out["score"] == 5
        assert net.post_calls == 1

    def test_active_short_circuits_without_chat(self):
        net = _FakeNet(gets=[(200, _heatmap(7))])
        out = rewards.run_active(net)
        assert out["result"] == "ACTIVE"
        assert net.post_calls == 0

    def test_chat_failure_keeps_attention(self):
        net = _FakeNet(
            gets=[(200, _heatmap(0))],
            posts=[(400, {"code": 11102, "msg": "model not found"})],
        )
        out = rewards.run_active(net)
        assert out["result"] == "NO_ACTIVITY"
        assert out["needs_attention"] is True
        assert "11102" in out["chat_error"]

    def test_chatted_but_not_scored_is_not_failure(self):
        """对话完成但日历还没记分: 不算失败(可能有延迟),交给下次调度确认。"""
        net = _FakeNet(
            gets=[(200, _heatmap(0)), (200, _heatmap(0))],
            posts=[_SSE_OK],
        )
        out = rewards.run_active(net)
        assert out["result"] == "NO_ACTIVITY"
        assert out["needs_attention"] is False
        assert out["chatted"] is True


class TestScheduleSkip:
    """静默调度时当天已成功签到 → 纯签到动作不执行、不 emit、不落库;
    轮询/成长类动作不跳——成长中心(旅行礼物/任务领奖)是独立收益源。"""

    def test_silent_auto_skips_when_done_today(self, monkeypatch):
        monkeypatch.setattr("auto_signin_kit.core.kit.actions.done_today",
                            lambda domain, account="default": True)
        emitted: list = []
        monkeypatch.setattr("auto_signin_kit.domains.client_signin.apps.workbuddy_cn.service.emit_result",
                            lambda out, **kw: emitted.append(out))
        from types import SimpleNamespace

        assert service.run_action("auto", SimpleNamespace(), silent=True) == 0
        assert emitted == []

    def test_poll_family_is_never_skipped(self, monkeypatch):
        """签了到 ≠ 旅行礼物已领: silent-poll 在 done_today=True 时仍真跑。"""
        monkeypatch.setattr("auto_signin_kit.core.kit.actions.done_today",
                            lambda domain, account="default": True)
        assert "silent-poll" not in service.SKIP_WHEN_DONE
        assert "poll" not in service.SKIP_WHEN_DONE
        assert "silent-growth" not in service.SKIP_WHEN_DONE
        # 行为验证: done_today=True 时 poll 走到 load_credentials 之后
        # (不再在 skip 处短路返回 0)——stub 凭据加载失败即证明已越过 skip
        monkeypatch.setattr(service, "load_credentials",
                            lambda *, auth_file=None: (
                                None, {"result": "NO_AUTH",
                                       "report": "越过 skip 的证据"}))
        from types import SimpleNamespace

        cfg = SimpleNamespace(budget_seconds=None, endpoint=None, taskbot=False)
        out = service.run_action("silent-poll", cfg, silent=True)
        assert out == 2  # 走到了凭据加载失败分支,而不是被 skip 静默吞掉

    def test_account_flows_into_ledger_envelope(self, monkeypatch):
        """SPEC-12: --account 全程透传;未声明的账户在动作分发前就报错。"""
        from types import SimpleNamespace

        from auto_signin_kit.core.config.schema import AccountRef

        seen: list = []

        def _capture(out, **kw):
            seen.append((out.get("result"), kw.get("account")))

        monkeypatch.setattr(service, "emit_result", _capture)
        cfg = SimpleNamespace(budget_seconds=None, endpoint=None, taskbot=False,
                              accounts=[AccountRef(name="main")])
        # 未知动作也在 resolve 之后才报——正好用它验证 account 进了信封
        assert service.run_action("bogus", cfg, account="main") == 2
        assert seen[-1] == ("ERROR", "main")
        # 未声明账户: FAILED(退出码 2),绝不落成别的账户的记录
        assert service.run_action("bogus", cfg, account="ghost") == 2
        assert seen[-1] == ("FAILED", None)


class TestAccountAuthFile:
    """SPEC-12 P2: 具名账户显式凭据文件——存在即读,缺失显式报账户名。"""

    def test_explicit_auth_file_is_loaded(self, tmp_path):
        from auto_signin_kit.domains.client_signin.apps.workbuddy_cn import credentials, service

        auth_file = tmp_path / "second-account.info"
        auth_file.write_text('{"auth": {"accessToken": "tok"}, "account": {"uid": "2"}}',
                             encoding="utf-8")
        found, looked_in = credentials.find_auth_file(auth_file=str(auth_file))
        assert found == auth_file and looked_in == [str(auth_file)]
        session, failure = service.load_credentials(auth_file=str(auth_file))
        assert failure is None and session["account"]["uid"] == "2"

    def test_explicit_missing_file_names_account(self, tmp_path, monkeypatch):
        from auto_signin_kit.domains.client_signin.apps.workbuddy_cn import service

        monkeypatch.setenv("WORKBUDDY_AUTH_FILE", str(tmp_path / "default.info"))
        session, failure = service.load_credentials(
            auth_file=str(tmp_path / "ghost.info"))
        assert session is None and failure["result"] == "NO_AUTH"
        assert "账户凭据文件不存在" in failure["report"]
        assert "ghost.info" in failure["report"]
