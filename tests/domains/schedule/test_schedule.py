"""schedule 域测试: 任务清单自洽 / service 编排 / CLI 冒烟(调度器全桩)。

调度器一律用桩: 单元测试绝不能真的往系统里注册/删除定时任务。
"""

import json

import pytest
from typer.testing import CliRunner

from auto_signin_kit.core import config as config_m
from auto_signin_kit.core.adapters.scheduler import SchedulerError
from auto_signin_kit.core.ports.scheduler import TaskStatus
from auto_signin_kit.domains.schedule import service
from auto_signin_kit.domains.schedule.cli import app
from auto_signin_kit.domains.schedule.data import TASKS, domain_of, known_domains, tasks_for

runner = CliRunner()

#: 各任务的**最低**执行时限(分钟);低于它就会在脚本自己收尾前被平台强杀。
#: 数字来自各域的时间预算上限: workbuddy 主入口 540s、轮询 240s、qoder/trae 240s。
MIN_LIMIT_MINUTES = {
    "WorkBuddyAutoSignin": 10,
    "WorkBuddyGrowthPoll": 5,
    "WorkBuddyActiveCheck": 5,
    "QoderDailyCredits": 5,
    "TraeDailyCheckin": 5,
}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """配置目录换到 tmp_path,测试永远读不到仓库真实配置。"""
    config_dir = tmp_path / "config"
    (config_dir / "domains").mkdir(parents=True)
    (config_dir / "domains" / "schedule.yaml").write_text(
        "enabled_tasks: []\n", encoding="utf-8"
    )
    monkeypatch.setattr(config_m, "resolve_config_dir", lambda: config_dir)
    return config_dir


class FakeScheduler:
    """桩调度器: 记录调用序列,不碰真实系统。"""

    platform = "fake"

    def __init__(self, installed=(), fail_on=()):
        self.installed = set(installed)
        self.fail_on = set(fail_on)
        self.calls = []

    def install(self, spec):
        self.calls.append(("install", spec.name))
        if spec.name in self.fail_on:
            raise SchedulerError("INSTALL_FAILED", "桩失败")
        self.installed.add(spec.name)

    def uninstall(self, name):
        self.calls.append(("uninstall", name))
        if name in self.fail_on:
            raise SchedulerError("UNINSTALL_FAILED", "桩失败")
        self.installed.discard(name)

    def status(self, name):
        self.calls.append(("status", name))
        return TaskStatus(name=name, installed=name in self.installed)

    def render(self, spec):
        self.calls.append(("render", spec.name))
        return "<fake %s>" % spec.name


class _Config:
    """够用的配置桩;service 读 ``enabled_tasks`` 与 ``tasks``。"""

    def __init__(self, enabled=(), tasks=None):
        self.enabled_tasks = list(enabled)
        self.tasks = tasks or {}


class _Override:
    """配置覆盖项桩;字段与 schema.TaskOverride 一致。"""

    def __init__(self, times=(), limit_minutes=None):
        self.times = list(times)
        self.limit_minutes = limit_minutes


class TestTaskCatalogue:
    def test_times_are_parseable_hhmm(self):
        from auto_signin_kit.core.adapters.scheduler.common import parse_hhmm

        for spec in TASKS:
            for value in spec.times:
                assert parse_hhmm(value) is not None, f"{spec.name}: {value}"

    def test_execution_limit_covers_domain_budget(self):
        """时限必须 ≥ 各域时间预算,否则任务会被平台在脚本收尾前强杀。"""
        for spec in TASKS:
            floor = MIN_LIMIT_MINUTES[spec.name]
            assert spec.limit_minutes >= floor, f"{spec.name} 时限 {spec.limit_minutes} < {floor}"

    def test_every_task_targets_a_known_domain(self):
        for spec in TASKS:
            assert domain_of(spec) in known_domains()

    def test_tasks_for_unknown_domain_is_empty(self):
        assert tasks_for("nope") == ()


class TestServiceOrchestration:
    def test_install_reports_partial_failure_and_keeps_going(self):
        """一个任务失败不中断其余——用户要看"哪些成了",不是第一条错误。"""
        scheduler = FakeScheduler(fail_on={"QoderDailyCredits"})
        done, failed = service.install(TASKS, scheduler)
        expected = [spec.name for spec in TASKS if spec.name != "QoderDailyCredits"]
        assert [item["name"] for item in done] == expected
        assert [item["name"] for item in failed] == ["QoderDailyCredits"]

    def test_install_dry_run_never_touches_system(self):
        scheduler = FakeScheduler()
        service.install(TASKS, scheduler, dry_run=True)
        assert scheduler.calls == []

    def test_uninstall_is_idempotent_when_nothing_installed(self):
        scheduler = FakeScheduler()
        done, failed = service.uninstall(TASKS, scheduler)
        assert failed == []
        assert len(done) == len(TASKS)

    def test_status_says_not_installed_without_error(self):
        scheduler = FakeScheduler()
        items = service.status(TASKS, scheduler)
        assert not any(item["installed"] for item in items)

    def test_run_action_unknown_action_returns_2(self, capsys):
        assert service.run_action("bogus", _Config()) == 2
        assert json.loads(capsys.readouterr().out)["result"] == "ERROR"

    def test_run_action_unknown_domain_returns_2(self, capsys):
        assert service.run_action("install", _Config(), domain="nope") == 2

    def test_times_and_limit_come_from_config(self):
        """调度时间由配置决定——改时间改 YAML 即可,不必动代码。"""
        config = _Config(tasks={"TraeDailyCheckin": _Override(("03:07",), 9)})
        trae = next(t for t in service.select_tasks(config, None)
                    if t.name == "TraeDailyCheckin")
        assert trae.times == ("03:07",)
        assert trae.limit_minutes == 9

    def test_unlisted_task_keeps_default_times(self):
        """只改一个任务时,其余任务沿用清单默认值,不必抄整张表。"""
        config = _Config(tasks={"TraeDailyCheckin": _Override(("03:07",))})
        picked = service.select_tasks(config, None)
        qoder = next(t for t in picked if t.name == "QoderDailyCredits")
        default = next(t for t in TASKS if t.name == "QoderDailyCredits")
        assert qoder.times == default.times
        assert qoder.limit_minutes == default.limit_minutes

    def test_enabled_tasks_narrows_selection(self):
        scheduler = FakeScheduler()
        assert service.run_action("install", _Config(enabled=["TraeDailyCheckin"]),
                                  scheduler=scheduler) == 0
        assert scheduler.installed == {"TraeDailyCheckin"}


class TestCli:
    def test_help_is_available(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for name in ("install", "uninstall", "status", "render"):
            assert name in result.output

    def test_install_dry_run_prints_all_tasks(self, monkeypatch):
        monkeypatch.setattr(service, "get_scheduler", lambda: FakeScheduler())
        result = runner.invoke(app, ["install", "--dry-run"])
        assert result.exit_code == 0
        assert json.loads(result.output)["result"] == "OK"

    def test_status_uses_injected_scheduler(self, monkeypatch):
        installed = {"TraeDailyCheckin"}
        monkeypatch.setattr(service, "get_scheduler", lambda: FakeScheduler(installed))
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        # 分母随清单变化,不写死——否则加一个任务就要回来改测试。
        assert payload["report"].startswith("已注册 1/%d" % len(TASKS))

    def test_render_prints_definition_without_installing(self, monkeypatch):
        fake = FakeScheduler()
        monkeypatch.setattr(service, "get_scheduler", lambda: fake)
        result = runner.invoke(app, ["render", "--domain", "qoder"])
        assert result.exit_code == 0
        assert "<fake QoderDailyCredits>" in result.output
        assert all(call[0] != "install" for call in fake.calls)


class TestSchema:
    def test_rejects_unknown_field(self, _isolate):
        """extra="forbid": 配置里混进 schema 之外的字段必须报错,不静默忽略。"""
        from pydantic import ValidationError

        from auto_signin_kit.domains.schedule.schema import ScheduleDomainConfig

        with pytest.raises(ValidationError):
            ScheduleDomainConfig.model_validate({"enabled_tasks": [], "nope": 1})

    def test_accepts_empty_config(self):
        from auto_signin_kit.domains.schedule.schema import ScheduleDomainConfig

        assert ScheduleDomainConfig.model_validate({}).enabled_tasks == []
