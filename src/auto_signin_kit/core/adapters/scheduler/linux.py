"""Linux 定时调度: 用户 crontab。

用带标记的块管理,而不是裸追加行: 块让"这个任务是本工具装的"这件事可判定,
``uninstall`` 才能精确只删自己的行,不动用户自己写的其它 crontab 条目。

时限在 cron 里同样没有对应项(与 launchd 一致),忽略;各域自带时间预算。
"""

from __future__ import annotations

from auto_signin_kit.core.adapters.native_helper import run_capture
from auto_signin_kit.core.adapters.scheduler.common import (
    SchedulerError,
    brief_output,
    exe_text,
    parse_hhmm,
    resolve_executable,
)
from auto_signin_kit.core.ports.scheduler import ScheduleSpec, TaskStatus

_TIMEOUT = 20.0
BEGIN = "# BEGIN auto-signin-kit:{name}"
END = "# END auto-signin-kit:{name}"
#: cron 环境极简(没有登录 shell 的 PATH),可执行文件一律用绝对路径。
_HEADER = "# 由 auto-signin-kit schedule 管理;手动改动会在下次 install 时被覆盖。"


def _strip_block(text: str, name: str) -> str:
    """移除该任务的块(含其后的空行),保留其余内容原样。"""
    begin, end = BEGIN.format(name=name), END.format(name=name)
    lines = text.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == begin:
            skipping = True
            continue
        if line.strip() == end:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out).strip("\n")


def _current_crontab() -> str:
    """读取现有 crontab;用户还没有 crontab 时返回空串(首次安装是常态)。"""
    code, out, _err = run_capture("crontab", ("-l",), timeout=_TIMEOUT)
    return out.decode("utf-8", "replace") if code == 0 else ""


def _write_crontab(text: str) -> None:
    """写回 crontab;失败抛 SchedulerError(INSTALL_FAILED/UNINSTALL_FAILED)。"""
    code, out, err = run_capture(
        "crontab", ("-",), input_bytes=text.encode("utf-8"), timeout=_TIMEOUT
    )
    if code != 0:
        raise SchedulerError("INSTALL_FAILED", brief_output(out, err))


class LinuxScheduler:
    """crontab 实现。"""

    platform = "linux"

    def install(self, spec: ScheduleSpec) -> None:
        """替换同名块后整表写回;已存在则更新。"""
        resolve_executable(spec.argv[0] if spec.argv else "")
        base = _strip_block(_current_crontab(), spec.name)
        block = self.render(spec).rstrip("\n")
        merged = f"{base}\n\n{block}\n" if base else f"{block}\n"
        _write_crontab(merged)

    def uninstall(self, name: str) -> None:
        """移除本任务的块;本就没注册时原样写回(幂等)。"""
        current = _current_crontab()
        stripped = _strip_block(current, name)
        if stripped == current.strip("\n"):
            return
        _write_crontab(f"{stripped}\n" if stripped else "")

    def status(self, name: str) -> TaskStatus:
        """以 crontab 里是否存在本任务的块为准。"""
        installed = BEGIN.format(name=name) in _current_crontab()
        return TaskStatus(name=name, installed=installed, detail="crontab")

    def render(self, spec: ScheduleSpec) -> str:
        """渲染本任务的 crontab 块(不落盘)。"""
        program = " ".join([exe_text(spec.argv[0]), *spec.argv[1:]]) if spec.argv else ""
        rows = []
        for value in spec.times:
            hour, minute = parse_hhmm(value)
            rows.append(f"{minute} {hour} * * * {program}")
        body = "\n".join(rows)
        return f"{BEGIN.format(name=spec.name)}\n{_HEADER}\n{body}\n{END.format(name=spec.name)}\n"
