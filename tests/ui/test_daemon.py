"""ui.daemon 后台运行生命周期测试(SPEC-10 §5)。

原则: uvicorn 绝不真起、绝不真杀无关进程——spawn/wait_ready 全桩或用
自建 socket,存活检测用短命 sleep 子进程验两态;真机验收留给手动步骤。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from auto_signin_kit.core.adapters.platform_info import IS_WINDOWS
from auto_signin_kit.core.errors import ConfigError
from auto_signin_kit.ui import cli as cli_mod
from auto_signin_kit.ui import daemon as daemon_mod

runner = CliRunner()


@pytest.fixture
def paths(monkeypatch, tmp_path):
    """PID/日志文件隔离到 tmp;返回 (pid 文件路径, 日志文件路径)。"""
    pid = tmp_path / "ui-serve.pid"
    log = tmp_path / "ui-serve.log"
    monkeypatch.setattr(daemon_mod, "pid_file", lambda: pid)
    monkeypatch.setattr(daemon_mod, "log_file", lambda: log)
    return pid, log


def _write_pid(pid_file, pid: int, port: int = 8688) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(json.dumps({
        "pid": pid, "host": "127.0.0.1", "port": port,
        "started_at": "2026-11-02T00:00:00+08:00",
    }), encoding="utf-8")


def _live_process() -> subprocess.Popen:
    return subprocess.Popen(  # noqa: S603 — 固定 argv 的短命探针
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------- PID 文件


def test_pid_roundtrip(paths):
    pid_file, _ = paths
    info = daemon_mod.PidInfo(pid=42, host="127.0.0.1", port=8688,
                              started_at="2026-11-02T00:00:00+08:00")
    daemon_mod.write_pid(info)
    assert daemon_mod.read_pid() == info


def test_read_pid_missing_or_corrupt(paths):
    pid_file, _ = paths
    assert daemon_mod.read_pid() is None  # 文件不存在
    pid_file.write_text("{not json", encoding="utf-8")
    assert daemon_mod.read_pid() is None  # 损坏
    pid_file.write_text('{"pid": 1}', encoding="utf-8")
    assert daemon_mod.read_pid() is None  # 字段缺失


def test_clear_pid_idempotent(paths):
    pid_file, _ = paths
    daemon_mod.clear_pid()  # 不存在也不报错
    _write_pid(pid_file, 1)
    daemon_mod.clear_pid()
    assert not pid_file.exists()


# ---------------------------------------------------------------- 存活检测


def test_is_alive_two_states():
    proc = _live_process()
    try:
        assert daemon_mod.is_alive(proc.pid) is True
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    assert daemon_mod.is_alive(proc.pid) is False


def test_is_alive_rejects_nonpositive():
    assert daemon_mod.is_alive(0) is False
    assert daemon_mod.is_alive(-1) is False


def test_matches_cmdline_requires_daemon_child():
    # 当前测试进程命令行没有 daemon-child,真实 ps 探测应判不匹配
    assert daemon_mod.matches_cmdline(os.getpid()) is bool(IS_WINDOWS)


@pytest.mark.skipif(IS_WINDOWS, reason="ps 探测分支仅 POSIX;Windows 直接跳过校验")
def test_matches_cmdline_detect_failure_is_conservative(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("ps 不可用")
    monkeypatch.setattr(daemon_mod.subprocess, "run", boom)
    assert daemon_mod.matches_cmdline(1) is False  # 探测失败按不匹配(不杀)


# ---------------------------------------------------------------- 状态判定


def test_inspect_absent(paths):
    assert daemon_mod.inspect() == ("absent", None)


def test_inspect_corrupt(paths):
    pid_file, _ = paths
    pid_file.write_text("garbage", encoding="utf-8")
    assert daemon_mod.inspect() == ("corrupt", None)


def test_inspect_stale(paths):
    pid_file, _ = paths
    proc = _live_process()
    proc.terminate()
    proc.wait(timeout=5)
    _write_pid(pid_file, proc.pid)
    state, info = daemon_mod.inspect()
    assert state == "stale" and info.pid == proc.pid


def test_inspect_running_and_foreign(paths, monkeypatch):
    pid_file, _ = paths
    _write_pid(pid_file, os.getpid())
    monkeypatch.setattr(daemon_mod, "matches_cmdline", lambda pid: True)
    state, _ = daemon_mod.inspect()
    assert state == "running"
    monkeypatch.setattr(daemon_mod, "matches_cmdline", lambda pid: False)
    assert daemon_mod.inspect()[0] == "foreign"


# ---------------------------------------------------------------- 启动


def test_start_daemon_rejects_existing_instance(paths, monkeypatch):
    info = daemon_mod.PidInfo(pid=1, host="127.0.0.1", port=8688, started_at="t")
    monkeypatch.setattr(daemon_mod, "inspect", lambda: ("running", info))
    with pytest.raises(ConfigError, match="已有实例"):
        daemon_mod.start_daemon("127.0.0.1", 8688, open_browser=False)


def test_start_daemon_success_message(paths, monkeypatch):
    _, log = paths
    monkeypatch.setattr(daemon_mod, "inspect", lambda: ("absent", None))
    monkeypatch.setattr(daemon_mod, "_spawn",
                        lambda host, port, open_browser: 12345)
    monkeypatch.setattr(daemon_mod, "_wait_ready", lambda pid, host, port: None)
    msg = daemon_mod.start_daemon("127.0.0.1", 8688, open_browser=False)
    assert "pid=12345" in msg and str(log) in msg


def test_start_daemon_clears_stale_record_before_spawn(paths, monkeypatch):
    # 残留记录不清的话,会被 _wait_ready 误当成新实例已就绪
    pid_file, _ = paths
    stale = daemon_mod.PidInfo(pid=424242, host="127.0.0.1", port=8688,
                               started_at="t")
    monkeypatch.setattr(daemon_mod, "inspect",
                        lambda: ("stale", stale))
    monkeypatch.setattr(daemon_mod, "_spawn",
                        lambda host, port, open_browser: 12345)
    monkeypatch.setattr(daemon_mod, "_wait_ready", lambda pid, host, port: None)
    daemon_mod.start_daemon("127.0.0.1", 8688, open_browser=False)
    assert not pid_file.exists()


def test_spawn_platform_flags_and_log_redirect(paths, monkeypatch):
    _, log = paths
    monkeypatch.setattr(daemon_mod, "_entry_argv", lambda: ["/fake/auto-signin-kit"])
    captured: dict = {}

    def fake_popen(argv, stdin=None, stdout=None, stderr=None, **kwargs):
        captured.update(argv=argv, stdin=stdin, stdout=stdout,
                        stderr=stderr, kwargs=kwargs)
        return SimpleNamespace(pid=999)

    monkeypatch.setattr(daemon_mod.subprocess, "Popen", fake_popen)
    assert daemon_mod._spawn("127.0.0.1", 8688, open_browser=False) == 999
    assert "--daemon-child" in captured["argv"] and "--open" not in captured["argv"]
    assert captured["stdout"] is not None
    assert captured["stderr"] == subprocess.STDOUT
    if IS_WINDOWS:
        assert captured["kwargs"]["creationflags"] & 0x8  # DETACHED_PROCESS
    else:
        assert captured["kwargs"]["start_new_session"] is True


def test_spawn_passes_open_flag(paths, monkeypatch):
    monkeypatch.setattr(daemon_mod, "_entry_argv", lambda: ["/fake/x"])
    captured: dict = {}
    monkeypatch.setattr(
        daemon_mod.subprocess, "Popen",
        lambda argv, **kw: (captured.update(argv=argv), SimpleNamespace(pid=1))[1])
    daemon_mod._spawn("127.0.0.1", 8688, open_browser=True)
    assert "--open" in captured["argv"]


def test_entry_argv_error_when_nothing_found(monkeypatch):
    monkeypatch.setattr(daemon_mod.sys, "argv", [""])
    monkeypatch.setattr(daemon_mod.shutil, "which", lambda name: None)
    with pytest.raises(ConfigError, match="可执行入口"):
        daemon_mod._entry_argv()


# ---------------------------------------------------------------- 就绪等待


def test_wait_ready_returns_once_pid_file_record_alive(paths, monkeypatch):
    # 就绪判定只信 PID 文件 + 记录进程存活,不要求等于 spawn 返回的 pid
    # (Windows 上入口是 exe 垫片,真正服务是它拉起的 python 子进程)
    pid_file, _ = paths
    monkeypatch.setattr(daemon_mod, "is_alive", lambda pid: True)
    _write_pid(pid_file, 999)
    daemon_mod._wait_ready(1, "127.0.0.1", 8688)  # 不超时即通过


def test_wait_ready_ignores_dead_pid_record(paths, monkeypatch):
    pid_file, _ = paths
    seen: list[int] = []

    def fake_alive(pid: int) -> bool:
        seen.append(pid)
        return pid != 999  # PID 文件记录 999 已死,拉起进程 1 活着

    monkeypatch.setattr(daemon_mod, "is_alive", fake_alive)
    monkeypatch.setattr(daemon_mod, "SPAWN_READY_SECONDS", 0.3)
    _write_pid(pid_file, 999)  # 记录的进程已死 → 不算就绪
    with pytest.raises(ConfigError, match="超时"):
        daemon_mod._wait_ready(1, "127.0.0.1", 8688)
    assert 999 in seen


def test_wait_ready_errors_when_child_dies(paths, monkeypatch):
    monkeypatch.setattr(daemon_mod, "is_alive", lambda pid: False)
    with pytest.raises(ConfigError, match="立即退出"):
        daemon_mod._wait_ready(1, "127.0.0.1", 1)


def test_wait_ready_timeout(paths, monkeypatch):
    monkeypatch.setattr(daemon_mod, "is_alive", lambda pid: True)
    monkeypatch.setattr(daemon_mod, "SPAWN_READY_SECONDS", 0.3)
    with pytest.raises(ConfigError, match="超时"):
        daemon_mod._wait_ready(1, "127.0.0.1", 8688)


# ---------------------------------------------------------------- 停止


def test_stop_when_absent_is_idempotent(paths):
    assert "已停止" in daemon_mod.stop_service()
    assert not daemon_mod.pid_file().exists()


def test_stop_cleans_stale_record(paths):
    pid_file, _ = paths
    proc = _live_process()
    proc.terminate()
    proc.wait(timeout=5)
    _write_pid(pid_file, proc.pid)
    msg = daemon_mod.stop_service()
    assert "残留" in msg and not pid_file.exists()


def test_stop_running_process(paths, monkeypatch):
    pid_file, _ = paths
    _write_pid(pid_file, os.getpid())
    monkeypatch.setattr(daemon_mod, "matches_cmdline", lambda pid: True)
    state = {"killed": False}
    monkeypatch.setattr(daemon_mod, "is_alive", lambda pid: not state["killed"])
    killed: list[int] = []
    monkeypatch.setattr(daemon_mod, "_kill",
                        lambda pid: (killed.append(pid),
                                     state.__setitem__("killed", True)))
    msg = daemon_mod.stop_service()
    assert "已停止(pid=%d)" % os.getpid() in msg
    assert killed == [os.getpid()] and not pid_file.exists()


@pytest.mark.skipif(IS_WINDOWS, reason="SIGKILL 升级路径仅 POSIX")
def test_stop_escalates_to_sigkill_when_stubborn(paths, monkeypatch):
    pid_file, _ = paths
    _write_pid(pid_file, 424242)
    monkeypatch.setattr(daemon_mod, "matches_cmdline", lambda pid: True)
    monkeypatch.setattr(daemon_mod, "is_alive", lambda pid: True)  # 杀不掉
    monkeypatch.setattr(daemon_mod, "_kill", lambda pid: None)
    monkeypatch.setattr(daemon_mod, "STOP_WAIT_SECONDS", 0.1)
    sigs: list[tuple[int, int]] = []
    monkeypatch.setattr(daemon_mod.os, "kill", lambda p, s: sigs.append((p, s)))
    with pytest.raises(ConfigError, match="SIGKILL"):
        daemon_mod.stop_service()
    import signal
    assert any(s == signal.SIGKILL for _, s in sigs)


# ---------------------------------------------------------------- status 文案


def test_status_all_states(paths, monkeypatch):
    pid_file, _ = paths
    assert "无运行记录" in daemon_mod.status_text()
    pid_file.write_text("bad", encoding="utf-8")
    assert "损坏" in daemon_mod.status_text()
    proc = _live_process()
    proc.terminate()
    proc.wait(timeout=5)
    _write_pid(pid_file, proc.pid)
    assert "残留" in daemon_mod.status_text()
    _write_pid(pid_file, os.getpid())
    monkeypatch.setattr(daemon_mod, "matches_cmdline", lambda pid: False)
    assert "被其他进程占用" in daemon_mod.status_text()
    monkeypatch.setattr(daemon_mod, "matches_cmdline", lambda pid: True)
    text = daemon_mod.status_text()
    assert "运行中" in text and "8688" in text


# ---------------------------------------------------------------- child


def test_run_child_writes_and_clears_pid(paths, monkeypatch):
    _, log = paths
    seen: dict = {}

    class _FakeServer:
        """模拟真实 uvicorn.Server: run() 置位 started 后持续服务。"""

        def __init__(self, config) -> None:
            self.started = False

        def run(self) -> None:
            self.started = True
            # 服务运行中,等主线程写 PID(与真实时序一致)
            deadline = time.monotonic() + 2
            while daemon_mod.read_pid() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            seen["pid_at_run"] = daemon_mod.read_pid()

    class _FakeUvicorn:
        Config = staticmethod(lambda app, host, port, log_level: object())
        Server = _FakeServer

    monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn)
    monkeypatch.setattr("auto_signin_kit.ui.app.create_app", lambda: object())
    daemon_mod.run_child("127.0.0.1", 8688, open_browser=False)
    assert seen["pid_at_run"] is not None
    assert seen["pid_at_run"].pid == os.getpid()  # 子进程身份由自己写
    assert not daemon_mod.pid_file().exists()  # 正常退出后清理


# ---------------------------------------------------------------- CLI


def test_cli_stop_absent_ok(paths):
    result = runner.invoke(cli_mod.app, ["stop"])
    assert result.exit_code == 0 and "已停止" in result.output


def test_cli_status_running(paths, monkeypatch):
    pid_file, _ = paths
    _write_pid(pid_file, os.getpid())
    monkeypatch.setattr(daemon_mod, "matches_cmdline", lambda pid: True)
    result = runner.invoke(cli_mod.app, ["status"])
    assert result.exit_code == 0 and "运行中" in result.output


def test_cli_serve_daemon_rejects_existing(paths, monkeypatch):
    info = daemon_mod.PidInfo(pid=1, host="127.0.0.1", port=8688, started_at="t")
    monkeypatch.setattr(daemon_mod, "inspect", lambda: ("running", info))
    result = runner.invoke(cli_mod.app, ["serve", "--daemon", "--no-open"])
    assert result.exit_code == 2 and "已有实例" in result.output


def test_cli_serve_daemon_success(paths, monkeypatch):
    monkeypatch.setattr(daemon_mod, "inspect", lambda: ("absent", None))
    monkeypatch.setattr(daemon_mod, "_spawn", lambda h, p, o: 7)
    monkeypatch.setattr(daemon_mod, "_wait_ready", lambda pid, h, p: None)
    result = runner.invoke(cli_mod.app, ["serve", "--daemon", "--no-open"])
    assert result.exit_code == 0 and "pid=7" in result.output


def test_cli_serve_rejects_daemon_combo(paths):
    result = runner.invoke(cli_mod.app,
                           ["serve", "--daemon", "--daemon-child", "--no-open"])
    assert result.exit_code == 2 and "不能同时使用" in result.output
