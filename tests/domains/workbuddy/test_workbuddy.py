"""workbuddy 域 CLI 冒烟: 无凭据路径与未知命令,不联网。"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auto_signin_kit.domains.workbuddy import rewards, service
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
