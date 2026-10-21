"""子进程调用封装: JSON-stdin 凭据助手与通用短命令,统一超时与管道上限。

两个入口都在 core/adapters 层——业务域禁止直接 import subprocess(架构门禁),

- ``run_json_stdin_helper``: WorkBuddy 式凭据助手(JSON 进、JSON 出,三管道
  并发搬运+硬超时+字节上限,捕获内容绝不进异常文本);
- ``run_capture``: Qoder 式短命令(runtime-info.exe),捕获 stdout/stderr,
  失败只给非零码,不把平台细节泄露给上层。
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from auto_signin_kit.core.errors import AdapterError

#: 助手类子进程的默认输入/输出、错误通道上限(字节)。
DEFAULT_INPUT_LIMIT = 65536
DEFAULT_OUTPUT_LIMIT = 65536
DEFAULT_ERR_LIMIT = 8192


class NativeHelperError(AdapterError):
    """凭据/设备助手失败;``reason`` 是稳定的错误类别,由调用方转译。"""

    def __init__(self, reason: str, message: str = "") -> None:
        """把 reason 作为主体,message 只补充类型名,不携带管道内容。"""
        super().__init__(reason if not message else f"{reason}: {message}")
        self.reason = reason


def run_json_stdin_helper(
    exe: str,
    script: str,
    request: dict[str, Any],
    *,
    timeout: float,
    allowed_reasons: frozenset[str],
    input_limit: int = DEFAULT_INPUT_LIMIT,
    output_limit: int = DEFAULT_OUTPUT_LIMIT,
    err_limit: int = DEFAULT_ERR_LIMIT,
    env_filter: Callable[[dict[str, str]], dict[str, str]] | None = None,
) -> dict[str, Any]:
    """把 ``request``(JSON)通过 stdin 交给 ``exe -e <script>``,读回 JSON 应答。

    全部管道都有字节上限、全程有硬超时;捕获到的数据绝不进异常文本。
    未产出协议应答/超限/超时统一报 HELPER_TIMEOUT 或 HELPER_PROTOCOL。
    """
    try:
        data = json.dumps(dict(request, version=1), ensure_ascii=True).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise NativeHelperError("INVALID_FORMAT", str(type(exc).__name__)) from None
    if len(data) > input_limit:
        raise NativeHelperError("INVALID_FORMAT")
    if timeout <= 0:
        raise NativeHelperError("HELPER_TIMEOUT")
    env = (env_filter or (lambda base: base))({})
    try:
        process = subprocess.Popen(  # noqa: S603  # 参数由调用方(域凭据逻辑)控制
            [exe, "-e", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise NativeHelperError("RUNTIME_UNAVAILABLE", str(type(exc).__name__)) from None
    output: dict[str, bytes] = {}
    failed = threading.Event()
    _communicate(process, data, output, failed, output_limit, err_limit, timeout)
    return _collect_reply(process, output, failed, allowed_reasons)


def _communicate(
    process: subprocess.Popen[Any],
    data: bytes,
    output: dict[str, bytes],
    failed: threading.Event,
    output_limit: int,
    err_limit: int,
    timeout: float,
) -> None:
    """并发读写三个管道,读满即杀;最后总 join + kill 兜底,超时报 HELPER_TIMEOUT。"""
    deadline = time.monotonic() + timeout

    def read_pipe(name: str, pipe: Any, limit: int) -> None:
        try:
            captured = pipe.read(limit + 1)
            if len(captured) > limit:
                failed.set()
                process.kill()
            else:
                output[name] = captured
        except OSError:
            failed.set()

    def write_pipe() -> None:
        try:
            process.stdin.write(data)
            process.stdin.close()
        except OSError:
            failed.set()

    workers = [
        threading.Thread(
            target=read_pipe, args=("stdout", process.stdout, output_limit), daemon=True
        ),
        threading.Thread(
            target=read_pipe, args=("stderr", process.stderr, err_limit), daemon=True
        ),
        threading.Thread(target=write_pipe, daemon=True),
    ]
    for worker in workers:
        worker.start()
    try:
        process.wait(timeout=max(0.001, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        raise NativeHelperError("HELPER_TIMEOUT") from None
    for worker in workers:
        worker.join(max(0.0, deadline - time.monotonic()))
    if any(worker.is_alive() for worker in workers):
        raise NativeHelperError("HELPER_TIMEOUT")
    for pipe in (process.stdin, process.stdout, process.stderr):
        with contextlib.suppress(Exception):
            pipe.close()
    if failed.is_set():
        raise NativeHelperError("HELPER_PROTOCOL")


def _collect_reply(
    process: subprocess.Popen[Any],
    output: dict[str, bytes],
    failed: threading.Event,
    allowed_reasons: frozenset[str],
) -> dict[str, Any]:
    """解析并校验助手应答;任何协议可疑之处宁可拒绝,不把可疑数据往外送。"""
    try:
        reply = json.loads(output.get("stdout", b"").decode("utf-8"))
    except (ValueError, UnicodeError):
        raise NativeHelperError("HELPER_PROTOCOL") from None
    if (
        not isinstance(reply, dict)
        or type(reply.get("version")) is not int
        or reply["version"] != 1
    ):
        raise NativeHelperError("HELPER_PROTOCOL")
    if reply.get("ok") is False and process.returncode == 1:
        reason = reply.get("reason")
        if isinstance(reason, str) and reason in allowed_reasons:
            raise NativeHelperError(reason)
        raise NativeHelperError("HELPER_PROTOCOL")
    if reply.get("ok") is not True or process.returncode != 0 or failed.is_set():
        raise NativeHelperError("HELPER_PROTOCOL")
    return reply


def run_capture(
    exe: str,
    args: tuple[str, ...] = (),
    *,
    timeout: float = 15.0,
    input_bytes: bytes = b"",
    cwd: Path | None = None,
) -> tuple[int, bytes, bytes]:
    """跑一条短命令并捕获输出;超时/OSError 统一返回非零码。"""
    kwargs: dict[str, Any] = {}
    if getattr(subprocess, "CREATE_NO_WINDOW", None) is not None:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(  # noqa: S603
            [exe, *args],
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, b"", b""
    return proc.returncode, proc.stdout, proc.stderr
