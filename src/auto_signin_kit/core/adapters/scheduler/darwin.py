"""macOS 定时调度: launchd 用户级代理(``~/Library/LaunchAgents``)。

时限(``limit_minutes``)在 launchd 里**没有对应项**——launchd 不提供
"跑多久就杀掉"的设置,这一点与 Windows 的 ``ExecutionTimeLimit`` 不同。
这里选择忽略而不是伪造: 各域自身带时间预算(见 ``core.httpclient``),
超时由进程自己收尾,比被平台强杀更可控(强杀会丢掉"已领到的部分"日志)。
"""

from __future__ import annotations

from pathlib import Path
from tempfile import gettempdir

from auto_signin_kit.core.adapters.native_helper import run_capture
from auto_signin_kit.core.adapters.scheduler.common import (
    SchedulerError,
    brief_output,
    exe_text,
    parse_hhmm,
    resolve_executable,
    xml_escape,
)
from auto_signin_kit.core.ports.scheduler import ScheduleSpec, TaskStatus

_TIMEOUT = 20.0
#: launchd label 前缀;反写域名避免与其它软件撞 label。
LABEL_PREFIX = "com.auto-signin-kit"


def _label(name: str) -> str:
    """任务名 → launchd label。"""
    return f"{LABEL_PREFIX}.{name}"


def _plist_path(name: str) -> Path:
    """用户级代理 plist 路径;目录按需创建(干净机器上可能不存在)。"""
    directory = Path.home() / "Library" / "LaunchAgents"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{_label(name)}.plist"


def _log_paths(name: str) -> tuple[str, str]:
    """launchd 不继承终端,stdout/stderr 必须落文件,否则输出直接丢失。"""
    base = Path(gettempdir()) / f"auto-signin-kit-{name}"
    return f"{base}.out", f"{base}.err"


class DarwinScheduler:
    """launchd 实现。"""

    platform = "darwin"

    def install(self, spec: ScheduleSpec) -> None:
        """写 plist → 先 unload 再 load(覆盖注册);失败抛 SchedulerError。"""
        resolve_executable(spec.argv[0] if spec.argv else "")
        path = _plist_path(spec.name)
        path.write_text(self.render(spec), encoding="utf-8")
        # 已加载的旧任务要先卸载,否则 load 会被视为重复注册而无效。
        run_capture("launchctl", ("unload", str(path)), timeout=_TIMEOUT)
        code, out, err = run_capture("launchctl", ("load", "-w", str(path)), timeout=_TIMEOUT)
        if code != 0:
            raise SchedulerError("INSTALL_FAILED", brief_output(out, err))

    def uninstall(self, name: str) -> None:
        """卸载并删除 plist;本就没注册时直接返回(幂等)。"""
        path = _plist_path(name)
        if not path.exists():
            return
        run_capture("launchctl", ("unload", str(path)), timeout=_TIMEOUT)
        path.unlink(missing_ok=True)

    def status(self, name: str) -> TaskStatus:
        """以 launchctl 是否真的加载为准,而不只看文件在不在。

        plist 存在但没 load 是常见残留(手动删过/系统升级后),只查文件会误报。
        """
        code, _out, _err = run_capture("launchctl", ("list", _label(name)), timeout=_TIMEOUT)
        return TaskStatus(name=name, installed=code == 0, detail=str(_plist_path(name)))

    def render(self, spec: ScheduleSpec) -> str:
        """渲染 plist 文本(不落盘)。"""
        program = [exe_text(spec.argv[0]), *spec.argv[1:]] if spec.argv else []
        args = "\n".join(f"    <string>{xml_escape(item)}</string>" for item in program)
        intervals = []
        for value in spec.times:
            hour, minute = parse_hhmm(value)
            intervals.append(
                "    <dict>\n"
                f"      <key>Hour</key><integer>{hour}</integer>\n"
                f"      <key>Minute</key><integer>{minute}</integer>\n"
                "    </dict>\n"
            )
        out_path, err_path = _log_paths(spec.name)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
            ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            "<dict>\n"
            "  <key>Label</key>\n"
            f"  <string>{xml_escape(_label(spec.name))}</string>\n"
            "  <key>ProgramArguments</key>\n"
            "  <array>\n"
            f"{args}\n"
            "  </array>\n"
            "  <key>StartCalendarInterval</key>\n"
            "  <array>\n"
            f"{''.join(intervals)}"
            "  </array>\n"
            "  <key>StandardOutPath</key>\n"
            f"  <string>{xml_escape(out_path)}</string>\n"
            "  <key>StandardErrorPath</key>\n"
            f"  <string>{xml_escape(err_path)}</string>\n"
            "</dict>\n"
            "</plist>\n"
        )
