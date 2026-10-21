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


def test_default_lands_in_project_logs_dir(monkeypatch):
    """默认落在项目 logs/ 目录,文件名按域名派生(<域名>.log),与来源一一对应。"""
    monkeypatch.delenv("WORKBUDDY_SIGNIN_LOG", raising=False)
    monkeypatch.delenv("AUTO_SIGNIN_KIT_LOG_DIR", raising=False)
    root = log_dir.project_root()
    assert root is not None, "测试本身在仓库里跑,必须能定位到项目根"
    resolved = Path(core_logging.result_log_path(
        "workbuddy", env_var="WORKBUDDY_SIGNIN_LOG"
    ))
    assert resolved.name == "workbuddy.log"
    assert resolved.parent == root / "logs"
    assert resolved.parent.is_dir()


def test_auto_log_dir_env_wins(tmp_path, monkeypatch):
    """AUTO_SIGNIN_KIT_LOG_DIR 显式配置时优先于项目目录(跨平台行为一致)。"""
    monkeypatch.delenv("QODER_SIGNIN_LOG", raising=False)
    monkeypatch.setenv("AUTO_SIGNIN_KIT_LOG_DIR", str(tmp_path / "explicit"))
    resolved = Path(core_logging.result_log_path(
        "qoder", env_var="QODER_SIGNIN_LOG", filename="qoder.log"
    ))
    assert resolved.parent == tmp_path / "explicit"
    assert resolved.parent.is_dir()


def test_platform_dir_is_fallback_without_project(tmp_path, monkeypatch):
    """非项目环境(定位不到仓库根)回退平台惯例目录,行为不能回归。"""
    monkeypatch.delenv("QODER_SIGNIN_LOG", raising=False)
    monkeypatch.delenv("AUTO_SIGNIN_KIT_LOG_DIR", raising=False)
    monkeypatch.setattr(log_dir, "project_root", lambda: None)
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
