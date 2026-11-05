"""web_signin 引擎测试(二): 执行流程(status→claim→确认) / run_action / 调度生成。

HTTP 全部经桩函数注入(monkeypatch ``executor.call``),不碰真实网络;
台账落库 monkeypatch ``core.kit.domain.emit_result``;限流退避与复查
等待的节奏表在测试里清零,否则一个用例要跑 200+ 秒。
"""

from __future__ import annotations

import json

import pytest
import yaml

from auto_signin_kit.core.httpclient import Budget
from auto_signin_kit.domains.web_signin import apps as apps_m
from auto_signin_kit.domains.web_signin import executor, service
from auto_signin_kit.domains.web_signin.authkit import Credential
from auto_signin_kit.domains.web_signin.executor import Executor
from auto_signin_kit.domains.web_signin.schedule_meta import _shift_time, _specs
from tests.domains.web_signin.test_schema_auth import BASE_DECL, make_decl


@pytest.fixture()
def no_sleep(monkeypatch):
    """清零限流退避与复查等待,让重试/确认路径可以秒回。"""
    monkeypatch.setattr(executor, "CLAIM_BACKOFFS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(executor, "CONFIRM_WAITS", (0.0, 0.0, 0.0))


def make_ex(decl=None, headers=None) -> Executor:
    """构造绑定桩凭据的执行器(认证已由 test_schema_auth 覆盖)。"""
    return Executor(decl or make_decl(),
                    Credential(token="tk", headers=headers or {}),  # noqa: S106 测试桩
                    Budget(240.0))


class TestExecutorFlow:
    @staticmethod
    def patch_call(monkeypatch, responses):
        """按调用顺序回放 (http_code, payload);记录每次调用的入参。"""
        calls: list[dict] = []
        queue = list(responses)

        def fake(url, **kwargs):
            calls.append({"url": url, **kwargs})
            return queue.pop(0)

        monkeypatch.setattr(executor, "call", fake)
        return calls

    def test_already_signed_skips_claim(self, monkeypatch):
        calls = self.patch_call(monkeypatch, [(200, {"code": 0, "checked_in": True,
                                                     "credits": 5}, None)])
        out = executor.claim_once(make_ex())
        assert out["result"] == "ALREADY" and out["credits"] == 5
        assert len(calls) == 1  # 只发 status,不发 claim

    def test_claim_then_confirm(self, monkeypatch, no_sleep):
        responses = [
            (200, {"code": 0, "checked_in": False}, None),   # status: 未签
            (200, {"code": 0}, None),                        # claim: 受理但不回显
            (200, {"code": 0, "checked_in": True, "credits": 10}, None),  # 复查确认
        ]
        self.patch_call(monkeypatch, responses)
        decl = make_decl(claim={"path": "/claim", "confirm_by_status": True})
        out = executor.claim_once(make_ex(decl))
        assert out["result"] == "CLAIMED" and out["credits"] == 10

    def test_claim_echo_checked_in_directly(self, monkeypatch):
        """服务端直接回显 checked_in=true 时无需复查。"""
        responses = [
            (200, {"code": 0, "checked_in": False}, None),
            (200, {"code": 0, "checked_in": True, "credits": 3}, None),
        ]
        self.patch_call(monkeypatch, responses)
        out = executor.claim_once(make_ex())
        assert out["result"] == "CLAIMED" and out["credits"] == 3

    def test_rate_limited_retries_then_succeeds(self, monkeypatch, no_sleep):
        responses = [
            (200, {"code": 0, "checked_in": False}, None),
            (200, {"code": 9074}, None),                     # 限流 → 退避重试
            (200, {"code": 0, "checked_in": True, "credits": 7}, None),
        ]
        self.patch_call(monkeypatch, responses)
        decl = make_decl(claim={"path": "/claim", "retry_on": [9074],
                                "confirm_by_status": False})
        out = executor.claim_once(make_ex(decl))
        assert out["result"] == "CLAIMED" and out["credits"] == 7

    def test_unmapped_business_code_is_error(self, monkeypatch):
        responses = [(200, {"code": 0, "checked_in": False}, None), (200, {"code": 5432}, None)]
        self.patch_call(monkeypatch, responses)
        out = executor.claim_once(make_ex())
        assert out["result"] == "ERROR" and out["code"] == 5432

    def test_mapped_auth_code(self, monkeypatch):
        responses = [(200, {"code": 0, "checked_in": False}, None), (200, {"code": 1001}, None)]
        self.patch_call(monkeypatch, responses)
        decl = make_decl(business_codes={1001: "AUTH_REJECTED"})
        out = executor.claim_once(make_ex(decl))
        assert out["result"] == "AUTH_REJECTED" and out["needs_attention"] is True

    def test_http_401_is_auth_rejected(self, monkeypatch):
        self.patch_call(monkeypatch, [(401, {}, None)])
        out = executor.claim_once(make_ex())
        assert out["result"] == "AUTH_REJECTED"

    def test_dry_run_never_claims(self, monkeypatch):
        responses = [(200, {"code": 0, "checked_in": False}, None)]
        calls = self.patch_call(monkeypatch, responses)
        out = executor.claim_once(make_ex(), dry_run=True)
        assert out["result"] == "DRY_RUN"
        assert all("/claim" not in c["url"] for c in calls)

    def test_no_status_goes_straight_to_claim(self, monkeypatch):
        """纯 claim 型应用(未声明 status): 单请求直达,数额由 amount_path 显式给出。"""
        calls = self.patch_call(monkeypatch, [(200, {"code": 0, "checked_in": True,
                                                     "credits": 2}, None)])
        decl = make_decl(status=None,
                         claim={"path": "/claim", "checked_in_path": "checked_in",
                                "amount_path": "credits"})
        out = executor.claim_once(make_ex(decl))
        assert out["result"] == "CLAIMED" and out["credits"] == 2
        assert len(calls) == 1

    def test_claim_request_carries_headers_and_body(self, monkeypatch):
        responses = [(200, {"code": 0, "checked_in": False}, None),
                     (200, {"code": 0, "checked_in": True}, None)]
        calls = self.patch_call(monkeypatch, responses)
        decl = make_decl(headers={"X-User-Region": "cn"},
                         claim={"path": "/claim", "body": {"req_source": 1}})
        executor.claim_once(make_ex(decl, headers={"Authorization": "Bearer tk"}))
        claim_call = calls[-1]
        assert claim_call["url"] == "https://api.demo.cn/claim"
        assert claim_call["headers"]["Authorization"] == "Bearer tk"
        assert claim_call["headers"]["X-User-Region"] == "cn"
        assert claim_call["payload"] == {"req_source": 1}


class TestRunAction:
    @pytest.fixture()
    def app_declared(self, tmp_path, monkeypatch):
        """tmp 配置目录里放一份 demo 声明(带真实凭据文件);台账 stub。"""
        root = tmp_path / "config"
        (root / "apps").mkdir(parents=True)
        creds = tmp_path / "creds.json"
        creds.write_text(json.dumps({"data": {"token": "tk-demo"}}), encoding="utf-8")
        data = {**BASE_DECL, "auth": {**BASE_DECL["auth"],
                                      "files": [str(creds)]}}
        (root / "apps" / "demo.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr(apps_m, "resolve_config_dir", lambda: root)
        monkeypatch.setattr("auto_signin_kit.core.kit.domain.emit_result",
                            lambda *a, **k: None)
        return root

    def test_requires_app_option(self):
        from auto_signin_kit.core.errors import CommandError

        with pytest.raises(CommandError, match="--app"):
            service.run_action("claim", None)

    def test_unknown_app_lists_available(self, tmp_path, monkeypatch):
        root = tmp_path / "config"
        (root / "apps").mkdir(parents=True)
        monkeypatch.setattr(apps_m, "resolve_config_dir", lambda: root)
        from auto_signin_kit.core.errors import ConfigMissingError

        with pytest.raises(ConfigMissingError, match="可用:"):
            service.run_action("claim", None, app="ghost")

    def test_end_to_end_claim(self, tmp_path, monkeypatch, app_declared):
        responses = [
            (200, {"code": 0, "checked_in": False}, None),
            (200, {"code": 0}, None),
            (200, {"code": 0, "checked_in": True, "credits": 8}, None),
        ]
        queue = list(responses)
        monkeypatch.setattr(executor, "call", lambda *a, **k: queue.pop(0))
        monkeypatch.setattr(executor, "CONFIRM_WAITS", (0.0, 0.0, 0.0))
        code = service.run_action("claim", None, app="demo")
        assert code == 0

    def test_end_to_end_dry_run(self, monkeypatch, app_declared):
        monkeypatch.setattr(executor, "call",
                            lambda *a, **k: (200, {"code": 0, "checked_in": True,
                                                   "credits": 5}, None))
        assert service.run_action("claim", None, app="demo", dry_run=True) == 0

    def test_list_lines_renders_apps(self, app_declared, capsys):
        lines = service.list_lines()
        assert any("demo" in line for line in lines)


class TestScheduleMeta:
    def test_specs_from_declarations(self, tmp_path, monkeypatch):
        root = tmp_path / "config"
        (root / "apps").mkdir(parents=True)
        (root / "apps" / "demo.yaml").write_text(
            yaml.safe_dump(BASE_DECL, allow_unicode=True), encoding="utf-8")
        second = {**BASE_DECL, "app_code": "beta", "display": "Beta",
                  "schedule": {"times": ["10:00"], "limit_minutes": 9}}
        (root / "apps" / "beta.yaml").write_text(
            yaml.safe_dump(second, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr(apps_m, "resolve_config_dir", lambda: root)
        specs = _specs()
        demo = next(s for s in specs if s.name == "WebSigninDemo")
        assert demo.argv == ("auto-signin-kit", "web_signin", "run", "--app", "demo",
                             "--silent")
        assert demo.times == ("09:05",) and demo.limit_minutes == 5
        beta = next(s for s in specs if s.name == "WebSigninBeta")
        assert beta.limit_minutes == 9

    def test_account_expansion_with_time_shift(self, tmp_path, monkeypatch):

        root = tmp_path / "config"
        (root / "apps").mkdir(parents=True)
        data = {**BASE_DECL,
                "schedule": {"times": ["09:05", "23:50"], "limit_minutes": 5},
                "accounts": [{"name": "second",
                              "auth_file": str(root / "second.json")}]}
        (root / "apps" / "demo.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr(apps_m, "resolve_config_dir", lambda: root)
        specs = {s.name: s for s in _specs()}
        assert set(specs) == {"WebSigninDemo", "WebSigninDemo@second"}
        shifted = specs["WebSigninDemo@second"].times
        assert shifted == ("09:06", "23:51")  # +1 分钟错峰,23:50+1 回绕正常
        assert specs["WebSigninDemo@second"].argv[-2:] == ("--account", "second")

    def test_shift_time_wraps_midnight(self):
        assert _shift_time("23:59", 2) == "00:01"
        assert _shift_time("00:00", 1440) == "00:00"
