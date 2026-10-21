"""Windows 定时调度: 任务计划程序(``schtasks``)。

一个任务一天要跑多次时**只能用任务 XML**——``schtasks /Create`` 的命令行
参数只认单个 ``/ST``,想挂多个触发时间就必须走 ``/XML``。这是本模块不直接用
命令行建任务的唯一原因,不是偏好。

``StartWhenAvailable`` 必须开: 触发时刻机器关机是常态,不开的话这次触发就
永远错过了,签到当天不会再补。
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

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

#: 域控环境里 schtasks 可能明显变慢,给足超时。
_TIMEOUT = 30.0


def _principal_user() -> str:
    """当前用户的 ``域\\用户`` 形式;取不到就退化为裸用户名。"""
    user = os.environ.get("USERNAME") or ""
    domain = os.environ.get("USERDOMAIN") or ""
    return f"{domain}\\{user}" if domain and user else user


class WindowsScheduler:
    """任务计划程序实现。"""

    platform = "windows"

    def install(self, spec: ScheduleSpec) -> None:
        """覆盖式注册(``/F``);已存在同名任务会被更新而不是报错。"""
        resolve_executable(spec.argv[0] if spec.argv else "")
        xml = self.render(spec)
        # schtasks /XML 只接受 UTF-16;直接用 UTF-8 会报"XML 格式错误"。
        with NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as handle:
            handle.write(xml)
            xml_path = handle.name
        try:
            code, out, err = run_capture(
                "schtasks", ("/Create", "/TN", spec.name, "/XML", xml_path, "/F"),
                timeout=_TIMEOUT,
            )
        finally:
            Path(xml_path).unlink(missing_ok=True)
        if code != 0:
            raise SchedulerError("INSTALL_FAILED", brief_output(out, err))

    def uninstall(self, name: str) -> None:
        """删除任务;本就没注册时直接返回(幂等)。"""
        if not self.status(name).installed:
            return
        code, out, err = run_capture(
            "schtasks", ("/Delete", "/TN", name, "/F"), timeout=_TIMEOUT
        )
        if code != 0:
            raise SchedulerError("UNINSTALL_FAILED", brief_output(out, err))

    def status(self, name: str) -> TaskStatus:
        """任务存在即 ``installed=True``;查不到是正常状态,不是错误。"""
        code, _out, _err = run_capture("schtasks", ("/Query", "/TN", name), timeout=_TIMEOUT)
        return TaskStatus(name=name, installed=code == 0)

    def render(self, spec: ScheduleSpec) -> str:
        """渲染任务 XML(UTF-16 写盘由 install 负责;这里只给文本)。"""
        command = xml_escape(exe_text(spec.argv[0]) if spec.argv else "")
        arguments = " ".join(xml_escape(arg) for arg in spec.argv[1:])
        today = date.today().isoformat()
        triggers = []
        for value in spec.times:
            hour, minute = parse_hhmm(value)
            triggers.append(
                "    <CalendarTrigger>\n"
                f"      <StartBoundary>{today}T{hour:02d}:{minute:02d}:00</StartBoundary>\n"
                "      <Enabled>true</Enabled>\n"
                "      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>\n"
                "    </CalendarTrigger>\n"
            )
        description = xml_escape(spec.description or spec.name)
        user = _principal_user()
        # 有 UserId 才能定位"以谁的身份跑";取不到就让它缺省为当前用户。
        user_id = f"      <UserId>{xml_escape(user)}</UserId>\n" if user else ""
        # LogonType 必须是 InteractiveToken,不能用 InteractiveTokenOrPassword:
        # 后者要求 schtasks 存一份凭据,非交互注册时会直接报"用户名或密码不正确"。
        # 代价是任务只在用户已登录的会话里跑——对签到场景正好: 桌面端的登录态
        # 本来就要求用户已登录,用户没登录时跑了也没有凭据可用。
        logon_type = "InteractiveToken"
        return (
            '<?xml version="1.0" encoding="UTF-16"?>\n'
            '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
            "  <RegistrationInfo>\n"
            f"    <Description>{description}</Description>\n"
            "  </RegistrationInfo>\n"
            "  <Triggers>\n"
            f"{''.join(triggers)}"
            "  </Triggers>\n"
            "  <Principals>\n"
            '    <Principal id="Author">\n'
            f"{user_id}"
            f"      <LogonType>{logon_type}</LogonType>\n"
            "      <RunLevel>LeastPrivilege</RunLevel>\n"
            "    </Principal>\n"
            "  </Principals>\n"
            "  <Settings>\n"
            "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
            "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
            "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
            "    <StartWhenAvailable>true</StartWhenAvailable>\n"
            f"    <ExecutionTimeLimit>PT{spec.limit_minutes}M</ExecutionTimeLimit>\n"
            "    <Enabled>true</Enabled>\n"
            "  </Settings>\n"
            '  <Actions Context="Author">\n'
            "    <Exec>\n"
            f"      <Command>{command}</Command>\n"
            f"      <Arguments>{arguments}</Arguments>\n"
            "    </Exec>\n"
            "  </Actions>\n"
            "</Task>\n"
        )
