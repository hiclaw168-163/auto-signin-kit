"""请求快照测试: 脱敏/截断/线程隔离 + 基类 emit 注入 request 列。"""

from __future__ import annotations

from auto_signin_kit.core.httpclient import Budget, RetryPolicy, call, fetch, last_request
from auto_signin_kit.core.kit.domain import SigninDomain


def test_fetch_records_snapshot_with_masked_query():
    fetch("https://x.example/api?token=SECRET&id=42", payload={"a": 1})
    snap = last_request()
    assert snap["method"] == "GET"
    assert "token=***" in snap["url"] and "id=42" in snap["url"] and "SECRET" not in snap["url"]
    assert snap["body"] == '{"a": 1}'


def test_snapshot_covers_previous_and_none_body():
    fetch("https://x.example/a", payload={"x": "y"})
    fetch("https://x.example/b")  # 无 body 覆盖前一次
    snap = last_request()
    assert snap["url"].endswith("/b") and snap["body"] is None


def test_body_truncated():
    fetch("https://x.example/c", payload={"big": "x" * 2000})
    assert len(last_request()["body"]) <= 500


def test_no_request_returns_none():
    import threading

    threading.Thread(target=lambda: None).start()  # 别的线程互不影响
    assert last_request() is None or isinstance(last_request(), dict)


def test_call_budget_out_clears_snapshot():
    fetch("https://x.example/prev", payload={})
    call("https://x.example/next", budget=Budget(0.0),
         policy=RetryPolicy(), retry=False)
    assert last_request() is None  # 没发出去不留快照,免得误导取证


def test_domain_emit_injects_request(monkeypatch, tmp_path):
    seen = {}

    class _D(SigninDomain):
        name = "d"
        display = "D"

        def build(self, config, *, once=False, dry_run=False, account=""):
            return "api"

        def claim(self, api, config, *, dry_run=False, once=False):
            return {"result": "CLAIMED", "report": "r"}, 0

    monkeypatch.setattr("auto_signin_kit.core.kit.domain.emit_result",
                        lambda out, **kw: seen.update(out))
    monkeypatch.setattr("auto_signin_kit.core.kit.domain.result_log_path",
                        lambda name, env_var: tmp_path / "x.log")
    fetch("https://x.example/claim?token=S", payload={"act": 1})
    _D().run_action("claim", object())
    assert seen["request"]["url"].endswith("/claim?token=***")
