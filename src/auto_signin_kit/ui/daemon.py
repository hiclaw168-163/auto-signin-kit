"""ui 服务的后台运行生命周期: PID 文件 / 存活检测 / spawn / 停止(SPEC-10)。

纯逻辑模块,不 import typer——CLI 只做参数解析,停止/状态逻辑住这里受门禁。

守护方式是 **spawn 自身子进程**: ``--daemon`` 用 Popen 重新拉起
``ui serve --daemon-child``(POSIX ``start_new_session=True``,Windows
``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP``),跨平台只差 Popen flags,
daemon 编排不依赖 uvicorn,可全桩单测。PID 文件由**子进程自己写**(启动即写),
消除"父写完子秒挂"的竞态;子进程被 SIGKILL 等异常退出会留下陈旧记录,
由 :func:`inspect` 统一按陈旧清理。
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from auto_signin_kit.core.adapters.log_dir import default_data_dir, default_log_dir
from auto_signin_kit.core.adapters.platform_info import IS_WINDOWS
from auto_signin_kit.core.errors import ConfigError

PID_FILE_NAME = "ui-serve.pid"
LOG_FILE_NAME = "ui-serve.log"

#: 运行中的 serve 实例注册的优雅关停钩子(应用重建时由 API 层触发)。
_shutdown_hook: Callable[[], None] | None = None

#: ``--daemon`` 拉起后等服务就绪的超时秒数(就绪判定 = 子进程写出 PID 文件)。
#: Windows 冷启动(python 解释器 + fastapi/pydantic 全量 import)实测可超 5s,
#: 给足余量——超时只是误报,子进程其实活着,但报错本身吓人且难排查。
SPAWN_READY_SECONDS = 20.0
#: stop 发终止信号后等待退出的秒数;POSIX 超时升级 SIGKILL。
STOP_WAIT_SECONDS = 10.0

#: 进程状态: running 存活且是本服务;stale 进程已死;foreign PID 被复用;
#: corrupt 状态文件损坏;absent 无状态文件。status/stop 共用本判定。
_RUNNING = "running"
_STALE = "stale"
_FOREIGN = "foreign"
_CORRUPT = "corrupt"
_ABSENT = "absent"

# Windows 进程探测/终止用的 Win32 常量(模块级,避开函数内 N806)
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_STILL_ACTIVE = 259


@dataclass
class PidInfo:
    """PID 文件内容;started_at 为 ISO 时间戳,status 直接展示。"""

    pid: int
    host: str
    port: int
    started_at: str

    def url(self) -> str:
        """服务访问地址。"""
        return "http://%s:%d" % (self.host, self.port)


def pid_file() -> Path:
    """PID 状态文件路径(数据目录,与台账同处)。"""
    return default_data_dir() / PID_FILE_NAME


def log_file() -> Path:
    """后台服务日志路径(日志目录,uvicorn stdout/stderr 落这里)。"""
    return default_log_dir() / LOG_FILE_NAME


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_pid(info: PidInfo) -> None:
    """原子写(临时文件 + replace),避免读到半个 JSON。"""
    target = pid_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": info.pid, "host": info.host,
        "port": info.port, "started_at": info.started_at,
    }
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".ui-serve.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def clear_pid() -> None:
    """删除状态文件;不存在不报错。"""
    pid_file().unlink(missing_ok=True)


def read_pid() -> PidInfo | None:
    """读并校验;文件不存在/损坏/字段缺失一律返回 None(按未运行处理)。"""
    try:
        raw = json.loads(pid_file().read_text(encoding="utf-8"))
        return PidInfo(
            pid=int(raw["pid"]), host=str(raw["host"]),
            port=int(raw["port"]), started_at=str(raw["started_at"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def is_alive(pid: int) -> bool:
    """进程存活探测: POSIX ``kill 0``,Windows ctypes 查退出码。"""
    if pid <= 0:
        return False
    if IS_WINDOWS:
        return _windows_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # 进程在,只是没权限发信号
    return True


def matches_cmdline(pid: int) -> bool:
    """尽力而为的进程身份校验: 命令行须含 ``daemon-child``。

    防 PID 复用后把无关进程当服务误杀;探测失败按不匹配处理(当陈旧清掉
    状态文件,不会杀任何进程,方向是安全的)。Windows 无轻量等价物,跳过。
    """
    if IS_WINDOWS:
        return True
    try:
        proc = subprocess.run(  # noqa: S603 — 固定 argv,无外部输入拼接
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "daemon-child" in (proc.stdout or "")


def inspect() -> tuple[str, PidInfo | None]:
    """判定当前状态,返回 ``(状态, PID 信息或 None)``;status/stop 共用。"""
    info = read_pid()
    if info is None:
        if pid_file().exists():
            return _CORRUPT, None
        return _ABSENT, None
    if not is_alive(info.pid):
        return _STALE, info
    if not matches_cmdline(info.pid):
        return _FOREIGN, info
    return _RUNNING, info


def start_daemon(host: str, port: int, open_browser: bool) -> str:
    """后台拉起服务并等服务就绪;已有实例在跑则显式报错,不静默复用。"""
    state, info = inspect()
    if state == _RUNNING:
        raise ConfigError(
            "已有实例在运行(pid=%d, %s)。先执行 auto-signin-kit ui stop 再启动"
            % (info.pid, info.url()))
    if state != _ABSENT:
        # 清掉 stale/corrupt/foreign 的残留记录: 就绪信号是"PID 文件出现",
        # 不先清的话旧记录会被当成新实例已就绪(与 stop 的 foreign 分支
        # 同口径——只清状态文件,不动被复用的进程)
        clear_pid()
    pid = _spawn(host, port, open_browser)
    _wait_ready(pid, host, port)
    return "已后台启动: pid=%d http://%s:%d\n日志: %s" % (pid, host, port, log_file())


def status_text() -> str:
    """人读的状态描述;只读,不动任何文件。"""
    state, info = inspect()
    if state == _RUNNING:
        return "运行中: pid=%d %s (started %s)\n日志: %s" % (
            info.pid, info.url(), info.started_at, log_file())
    if state == _ABSENT:
        return "已停止(无运行记录)"
    if state == _CORRUPT:
        return "已停止(状态文件损坏,可删除 %s)" % pid_file()
    if state == _STALE:
        return "已停止(上次未正常退出,残留记录 pid=%d 已清理)" % info.pid
    return "已停止(记录 pid=%d 已被其他进程占用,未动它)" % info.pid


def stop_service() -> str:
    """停止后台实例;幂等——未运行也返回成功语义的消息。"""
    state, info = inspect()
    if state == _RUNNING:
        _kill(info.pid)
        deadline = time.monotonic() + STOP_WAIT_SECONDS
        while time.monotonic() < deadline and is_alive(info.pid):
            time.sleep(0.1)
        if is_alive(info.pid):
            if IS_WINDOWS:
                raise ConfigError(
                    "pid=%d 终止后仍未退出(Windows 无更强制手段,检查权限)" % info.pid)
            os.kill(info.pid, signal.SIGKILL)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and is_alive(info.pid):
                time.sleep(0.1)
            if is_alive(info.pid):
                raise ConfigError("pid=%d SIGKILL 后仍未退出" % info.pid)
        clear_pid()
        return "已停止(pid=%d)" % info.pid
    # 非 running 态: 残留记录顺手清掉,但原因按 inspect 结果说,别清完再查
    clear_pid()
    if state == _ABSENT:
        return "已停止(无运行记录)"
    if state == _CORRUPT:
        return "已停止(状态文件损坏,已清理)"
    if state == _STALE:
        return "已停止(上次未正常退出,残留记录 pid=%d 已清理)" % info.pid
    return "已停止(记录 pid=%d 已被其他进程占用,已清理)" % info.pid


def request_shutdown() -> bool:
    """请求运行中的 serve 优雅退出(uvicorn 跑完当前请求再停)。

    供应用重建流程调用(SPEC-11): 新域生效需要重装 tool,serve 必须
    先让位。无运行实例(如非 daemon 模式)返回 False。
    """
    hook = _shutdown_hook
    if hook is None:
        return False
    hook()
    return True


def run_child(host: str, port: int, open_browser: bool) -> None:
    """``--daemon-child`` 的主体: 起服务 → **就绪后**写 PID → 开浏览器 → 阻塞。

    PID 文件必须在服务真正可服务之后写(uvicorn ``started`` 标志),否则
    "端口被占导致秒退"会被父进程误判为就绪——端口探测会被占端口的**别人**
    骗过,PID 文件只会由本进程在就绪后写,是可信信号。
    """
    try:
        import uvicorn
    except ImportError as exc:
        raise ConfigError(
            "Web 管理台依赖未安装(fastapi/uvicorn 属于 [web] extra)。"
            "执行: uv tool install 'auto-signin-kit[web]' --force"
        ) from exc
    from auto_signin_kit.ui.app import create_app

    server = uvicorn.Server(
        uvicorn.Config(create_app(), host=host, port=port, log_level="info"))
    thread = threading.Thread(target=server.run, daemon=True,
                              name="ask-ui-serve")
    thread.start()
    deadline = time.monotonic() + SPAWN_READY_SECONDS
    while time.monotonic() < deadline and not server.started:
        if not thread.is_alive():
            raise ConfigError("服务线程启动即退出(典型原因: 端口被占用),日志: %s"
                              % log_file())
        time.sleep(0.1)
    if not server.started:
        raise ConfigError("服务 %ds 内未就绪,日志: %s"
                          % (int(SPAWN_READY_SECONDS), log_file()))

    global _shutdown_hook

    def _hook() -> None:
        server.should_exit = True

    _shutdown_hook = _hook
    write_pid(PidInfo(pid=os.getpid(), host=host, port=port, started_at=_now()))
    try:
        if open_browser:
            import webbrowser

            webbrowser.open("http://%s:%d" % (host, port))
        thread.join()  # 服务线程退出(含 SIGTERM 触发的优雅关闭)即结束
    finally:
        _shutdown_hook = None
        current = read_pid()
        if current is not None and current.pid == os.getpid():
            clear_pid()


def _spawn(host: str, port: int, open_browser: bool) -> int:
    """拉起 detached 子进程;stdio 全进日志文件,返回子进程 PID。"""
    argv = _entry_argv() + [
        "ui", "serve", "--daemon-child",
        "--host", host, "--port", str(port),
    ] + (["--open"] if open_browser else [])
    log_path = log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        try:
            if IS_WINDOWS:
                proc = subprocess.Popen(  # noqa: S603 — argv 自拼装,无外部输入
                    argv, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                proc = subprocess.Popen(  # noqa: S603
                    argv, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True,
                )
        except OSError as exc:
            raise ConfigError("后台拉起失败: %s" % exc) from exc
    return proc.pid


def _wait_ready(spawned_pid: int, host: str, port: int) -> None:
    """等子进程就绪: PID 文件出现**且**其中记录的进程存活。

    不做端口探测——占端口的可能是别人,连得上不代表**本**服务就绪;
    PID 文件是唯一可信的就绪信号。注意**不能**要求 PID 文件里的 pid
    等于 spawn 返回的 pid: Windows 上 uv tool 的可执行入口是 trampoline
    (exe 垫片),``--daemon-child`` 跑在垫片拉起的 python 子进程里,
    ``os.getpid()``(写 PID 文件的值)≠ 垫片的 pid,按相等判定会永远
    超时(服务其实已就绪)。旧记录污染由 :func:`start_daemon` 在 spawn
    前清理解决,本函数见到的 PID 文件只会是新子进程写的。
    """
    deadline = time.monotonic() + SPAWN_READY_SECONDS
    while time.monotonic() < deadline:
        info = read_pid()
        if info is not None and is_alive(info.pid):
            return
        if not is_alive(spawned_pid):
            raise ConfigError(
                "后台服务启动后立即退出(典型原因: 端口被占用),日志: %s" % log_file())
        time.sleep(0.1)
    current = read_pid()
    detail = (";PID 文件记录 pid=%d(垫片会再拉起真正的服务子进程)"
              % current.pid) if current is not None else ""
    raise ConfigError(
        "等待服务就绪超时(%ds),拉起进程 pid=%d 仍在运行%s,日志: %s"
        % (int(SPAWN_READY_SECONDS), spawned_pid, detail, log_file()))


def _entry_argv() -> list[str]:
    """找与当前进程同款的可执行入口,供拉起 ``--daemon-child``。"""
    raw = sys.argv[0]
    if raw:
        try:
            exe = Path(raw).resolve()
        except OSError:  # pragma: no cover — resolve 失败按找不到入口走
            exe = None
        if exe is not None and exe.is_file():
            return [str(exe)]
    which = shutil.which("auto-signin-kit")
    if which:
        return [which]
    raise ConfigError("找不到 auto-signin-kit 可执行入口,无法后台拉起")


def _kill(pid: int) -> None:
    """发终止信号: POSIX SIGTERM;Windows 直接 TerminateProcess(本就强制)。"""
    if IS_WINDOWS:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
        if not handle:
            raise ConfigError("无法打开进程 pid=%d(可能已退出或权限不足)" % pid)
        try:
            if not kernel32.TerminateProcess(handle, 1):
                raise ConfigError("终止进程 pid=%d 失败(权限不足?)" % pid)
        finally:
            kernel32.CloseHandle(handle)
    else:
        os.kill(pid, signal.SIGTERM)


def _windows_alive(pid: int) -> bool:
    import ctypes
    import ctypes.wintypes as wt

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = wt.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
