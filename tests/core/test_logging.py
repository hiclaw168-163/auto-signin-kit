"""core.logging.result_log_path: 域结果日志统一入口的环境变量优先级。"""

from pathlib import Path

from auto_signin_kit.core import logging as core_logging
from auto_signin_kit.core.adapters import log_dir
from auto_signin_kit.core.adapters.platform_info import IS_MACOS, IS_WINDOWS


def test_env_override_wins(tmp_path, monkeypatch):
    """域环境变量显式指定时必须原样使用(不支持时用户会踩坑)。"""
    custom = tmp_path / "custom.log"
    monkeypatch.setenv("WORKBUDDY_SIGNIN_LOG", str(custom))
    assert core_logging.result_log_path(
        "workbuddy", env_var="WORKBUDDY_SIGNIN_LOG", filename="signin.log"
    ) == custom


def test_default_lands_in_platform_log_dir(tmp_path, monkeypatch):
    """没指定环境变量时必须落在当前平台的日志目录,而不是 cwd/仓库根(排错起点)。"""
    monkeypatch.delenv("QODER_SIGNIN_LOG", raising=False)
    if IS_WINDOWS:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        expected = log_dir.app_data_root() / "logs"
    elif IS_MACOS:
        expected = Path.home() / "Library" / "Logs" / log_dir.APP_DIR_NAME
    else:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        expected = log_dir.app_data_root() / "log"
    resolved = Path(core_logging.result_log_path(
        "qoder", env_var="QODER_SIGNIN_LOG", filename="qoder.log"
    ))
    assert resolved.name == "qoder.log"
    assert resolved.parent == expected
    assert resolved.parent.is_dir()
