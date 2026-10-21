"""qoder 域 CLI 冒烟: 无登录态/doctor 路径,不联网。"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auto_signin_kit.domains.qoder import service
from auto_signin_kit.domains.qoder.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """令牌/数据目录环境变量清空 + 日志隔离,保证用例不依赖真实 Qoder 安装。"""
    for name in ("QODER_TOKEN", "QODER_AUTH_DIR", "QODER_UMID_EXE"):
        monkeypatch.delenv(name, raising=False)
    # 数据目录指到一个空目录: 桌面端"目录在但未登录"的 NO_AUTH 分支。
    monkeypatch.setenv("QODER_AUTH_DIR", str(tmp_path / "empty"))
    monkeypatch.setattr(service, "log_path", lambda: Path(tmp_path) / "qoder.log")


def test_claim_without_auth_reports_no_auth_with_exit_2():
    """无登录态: NO_AUTH + looked_in,退出码 2。"""
    result = runner.invoke(app, ["auto"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["result"] == "NO_AUTH"


def test_doctor_offline_reports_clearly():
    """doctor 离线体检: 不联网也能给出明确的分类结果与探测路径。"""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code in (1, 2)
    payload = json.loads(result.stdout)
    assert payload["result"] in ("NO_AUTH", "AUTH_ERROR")
    assert payload.get("looked_in") or payload.get("token_source")


def test_dry_run_flag_is_accepted(monkeypatch):
    """--dry-run 参数能被正确解析;无登录态时仍应 NO_AUTH(不假装可领)。"""
    result = runner.invoke(app, ["auto", "--dry-run"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["result"] == "NO_AUTH"
