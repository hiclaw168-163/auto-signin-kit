"""定时调度适配器测试: 三种实现的渲染输出。

只测纯函数(``render`` / 块裁剪),**不调用系统命令**——注册/删除真实任务的
路径属集成范畴,单元测试绝不碰用户的系统。
"""

import pytest

from auto_signin_kit.core.adapters.scheduler.darwin import DarwinScheduler
from auto_signin_kit.core.adapters.scheduler.linux import LinuxScheduler, _strip_block
from auto_signin_kit.core.adapters.scheduler.windows import WindowsScheduler
from auto_signin_kit.core.ports.scheduler import ScheduleSpec

SPEC = ScheduleSpec(
    name="T",
    argv=("auto-signin-kit", "qoder", "auto"),
    times=("09:05", "21:05"),
    limit_minutes=5,
)


class TestWindowsRender:
    def test_uses_interactive_token_not_password(self):
        """回归: ``InteractiveTokenOrPassword`` 会让 schtasks 报"用户名或密码不正确"。

        这是真实踩过的坑——非交互注册时它要求存一份凭据,而我们拿不到也不该拿。
        """
        xml = WindowsScheduler().render(SPEC)
        assert "<LogonType>InteractiveToken</LogonType>" in xml
        assert "InteractiveTokenOrPassword" not in xml

    def test_one_trigger_per_time(self):
        xml = WindowsScheduler().render(SPEC)
        assert xml.count("<CalendarTrigger>") == 2

    def test_execution_limit_matches_spec(self):
        assert "PT5M" in WindowsScheduler().render(SPEC)

    def test_escapes_ampersand_in_path(self):
        spec = ScheduleSpec(name="T", argv=("C:\\a&b\\kit.exe", "qoder"), times=("09:05",))
        assert "a&amp;b" in WindowsScheduler().render(spec)

    def test_start_when_available_is_on(self):
        """触发时刻关机是常态,不开就永远错过当天这一次。"""
        assert "<StartWhenAvailable>true</StartWhenAvailable>" in WindowsScheduler().render(SPEC)


class TestDarwinRender:
    def test_label_is_prefixed(self):
        assert "com.auto-signin-kit.T" in DarwinScheduler().render(SPEC)

    def test_one_calendar_interval_per_time(self):
        plist = DarwinScheduler().render(SPEC)
        assert plist.count("<key>Hour</key>") == 2

    def test_redirects_output_to_files(self):
        """launchd 不继承终端,不落文件输出就丢了。"""
        plist = DarwinScheduler().render(SPEC)
        assert "<key>StandardOutPath</key>" in plist
        assert "<key>StandardErrorPath</key>" in plist


class TestLinuxRender:
    def test_block_is_bounded_by_markers(self):
        cron = LinuxScheduler().render(SPEC)
        assert cron.startswith("# BEGIN auto-signin-kit:T")
        assert cron.rstrip().endswith("# END auto-signin-kit:T")

    def test_one_row_per_time(self):
        cron = LinuxScheduler().render(SPEC)
        assert "5 9 * * *" in cron
        assert "5 21 * * *" in cron


class TestLinuxStripBlock:
    def test_removes_only_own_block(self):
        """只删自己的行,不动用户自己写的 crontab 条目。"""
        merged = "0 3 * * * /usr/bin/backup\n" + LinuxScheduler().render(SPEC)
        kept = _strip_block(merged, "T")
        assert "/usr/bin/backup" in kept
        assert "# BEGIN auto-signin-kit:T" not in kept

    def test_keeps_other_tasks(self):
        other = ScheduleSpec(name="Other", argv=("auto-signin-kit", "trae"), times=("08:00",))
        merged = LinuxScheduler().render(SPEC) + LinuxScheduler().render(other)
        kept = _strip_block(merged, "T")
        # 只按块标记断言: 命令行里的可执行文件会被解析成绝对路径,断言原串会脆。
        assert "# BEGIN auto-signin-kit:Other" in kept
        assert "# BEGIN auto-signin-kit:T" not in kept


@pytest.mark.parametrize("bad", ["25:00", "9:5", "abc", ""])
def test_bad_time_is_rejected(bad):
    """时间非法要显式报错,不做静默兜底。"""
    from auto_signin_kit.core.adapters.scheduler.common import SchedulerError, parse_hhmm

    with pytest.raises(SchedulerError):
        parse_hhmm(bad)
