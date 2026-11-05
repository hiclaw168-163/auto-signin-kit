"""SigninDomain 模板方法基类测试: 外壳语义逐项锁定。"""

from __future__ import annotations

from typing import ClassVar

import pytest

from auto_signin_kit.core.config.schema import AccountRef
from auto_signin_kit.core.config.schema import DomainConfig as _DomainConfig
from auto_signin_kit.core.errors import ConfigError
from auto_signin_kit.core.kit.domain import DomainError, SigninDomain


class _Capture(SigninDomain):
    """记录 emit 调用的最小子类。"""

    name = "demo"
    display = "Demo"
    emitted: ClassVar[list] = []

    def _emit(self, out, label, *, silent, account=None):
        self.emitted.append((out, label, silent, account))


class _Ok(_Capture):
    def build(self, config, *, once=False, dry_run=False, account=""):
        return "api"

    def claim(self, api, config, *, dry_run=False, once=False):
        return {"result": "CLAIMED", "report": "ok"}, 0

    def status(self, api, config):
        return {"result": "OK", "report": "ok"}, 0


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.setattr("auto_signin_kit.core.kit.domain.result_log_path",
                        lambda name, env_var: tmp_path / "x.log")
    monkeypatch.setattr("auto_signin_kit.core.kit.domain.emit_result",
                        lambda *a, **k: None)
    _Capture.emitted.clear()


def test_full_flow_claim_and_status():
    d = _Ok()
    assert d.run_action("claim", cfg := object(), command="auto") == 0
    out, label, _silent, _acct = _Capture.emitted[-1]
    assert out["result"] == "CLAIMED" and label == "auto" and not _silent
    assert d.run_action("status", cfg) == 0


def test_unknown_action_returns_2():
    d = _Ok()
    assert d.run_action("nope", object()) == 2
    assert "未知命令" in _Capture.emitted[-1][0]["report"]


def test_doctor_default_stub():
    d = _Ok()
    assert d.run_action("doctor", object()) == 2
    assert "体检未实现" in _Capture.emitted[-1][0]["report"]


def test_domain_failure_mapped_with_extra():
    class _F(_Ok):
        def build(self, config, *, once=False, dry_run=False, account=""):
            raise DomainError("NO_AUTH", "没有登录态", exit_code=2,
                                extra={"looked_in": ["a", "b"]})

    assert _F().run_action("claim", object()) == 2
    out = _Capture.emitted[-1][0]
    assert out["result"] == "NO_AUTH" and out["looked_in"] == ["a", "b"]


def test_legacy_auto_error_fallback():
    class _F(_Ok):
        def build(self, config, *, once=False, dry_run=False, account=""):
            raise ConfigError("配置坏了")

    assert _F().run_action("claim", object()) == 2  # exit_code=2 → NO_AUTH
    assert _Capture.emitted[-1][0]["result"] == "NO_AUTH"


def test_unimplemented_hooks_return_2():
    class _Partial(SigninDomain):
        name = "p"
        display = "P"

        def build(self, config, *, once=False, dry_run=False, account=""):
            return "api"

    d = _Partial()
    assert d.run_action("claim", object()) == 2  # claim 钩子未实现
    assert d.run_action("status", object()) == 2


def test_skip_done_today_path(monkeypatch):
    monkeypatch.setattr("auto_signin_kit.core.kit.domain.skip_done_today",
                        lambda *a, **k: True)
    assert _Ok().run_action("claim", object(), silent=True) == 0
    assert not _Capture.emitted  # 跳过不落库


# --------------------------------------------------------------- SPEC-12 账户维

def test_run_action_passes_account_to_emit():
    cfg = _DomainConfig(accounts=[AccountRef(name="main")])
    assert _Ok().run_action("claim", cfg, account="main") == 0
    assert _Capture.emitted[-1][3] == "main"  # account 进了台账信封


def test_undeclared_account_fails_fast():
    cfg = _DomainConfig(accounts=[AccountRef(name="main")])
    assert _Ok().run_action("claim", cfg, account="ghost") == 2
    out = _Capture.emitted[-1][0]
    assert out["result"] == "FAILED" and "ghost" in out["report"]


def test_disabled_account_skips_silent_but_allows_manual():
    cfg = _DomainConfig(accounts=[AccountRef(name="main", enabled=False)])
    # 静默(调度)路径: 停用账户不执行、不落库
    assert _Ok().run_action("claim", cfg, silent=True, account="main") == 0
    assert not _Capture.emitted
    # 手动路径: 显式 --account 指名道姓,停用也放行真跑
    assert _Ok().run_action("claim", cfg, account="main") == 0
    assert _Capture.emitted[-1][0]["result"] == "CLAIMED"
