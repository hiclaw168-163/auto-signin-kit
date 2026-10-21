"""core.logging.result_log_path: 域结果日志统一入口的环境变量优先级。"""

from pathlib import Path

from auto_signin_kit.core import logging as core_logging


def test_env_override_wins(tmp_path, monkeypatch):
    """域环境变量显式指定时必须原样使用(不支持时用户会踩坑)。"""
    custom = tmp_path / "custom.log"
    monkeypatch.setenv("WORKBUDDY_SIGNIN_LOG", str(custom))
    assert core_logging.result_log_path(
        "workbuddy", env_var="WORKBUDDY_SIGNIN_LOG", filename="signin.log"
    ) == custom


def test_default_lands_in_platform_log_dir(tmp_path, monkeypatch):
    """没指定环境变量时必须落在平台日志目录,而不是 cwd/仓库根(排错起点)。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("QODER_SIGNIN_LOG", raising=False)
    path = core_logging.result_log_path("qoder", env_var="QODER_SIGNIN_LOG", filename="qoder.log")
    resolved = Path(path)
    assert resolved.name == "qoder.log"
    assert resolved.parent == tmp_path / "auto-signin-kit" / "logs"
    assert resolved.parent.is_dir()
