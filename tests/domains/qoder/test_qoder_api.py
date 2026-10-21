"""qoder api 单元测试: 预算解析 / 开关环境变量 / 请求形状 / 脱敏登记(不联网)。"""

from types import SimpleNamespace

from auto_signin_kit.core.httpclient import Budget
from auto_signin_kit.domains.qoder import api as api_mod
from auto_signin_kit.domains.qoder.api import (
    BUDGET_CEILING,
    CLAIMABLE_URL,
    REQUEST_TIMEOUT,
    RETRY_POLICY,
    Api,
    mark_token_seen,
    parse_budget,
)


def _config(budget_seconds=None):
    """最小 config 鸭子类型,只覆盖 parse_budget/build_api 用到的字段。"""
    return SimpleNamespace(budget_seconds=budget_seconds, base_url="https://cfg.example")



class TestParseBudget:
    def test_env_absent_uses_config_value(self, monkeypatch):
        """未设置环境变量时用 config.budget_seconds,无告警。"""
        monkeypatch.delenv("QODER_BUDGET_SECONDS", raising=False)
        assert parse_budget(_config(60)) == (60, None)

    def test_config_none_falls_to_ceiling(self, monkeypatch):
        """config 没配预算时回落 240 上限。"""
        monkeypatch.delenv("QODER_BUDGET_SECONDS", raising=False)
        assert parse_budget(_config(None)) == (BUDGET_CEILING, None)

    def test_env_overrides_config(self, monkeypatch):
        """环境变量合法时优先于 config。"""
        monkeypatch.setenv("QODER_BUDGET_SECONDS", "30")
        assert parse_budget(_config(120)) == (30.0, None)

    def test_non_numeric_falls_back_with_warning(self, monkeypatch):
        """非数字显式回落 config 默认值并给告警,不静默吞。"""
        monkeypatch.setenv("QODER_BUDGET_SECONDS", "abc")
        value, warning = parse_budget(_config(90))
        assert value == 90
        assert warning is not None and "不是数字" in warning

    def test_non_positive_falls_back_with_warning(self, monkeypatch):
        """0/负数回落默认值并告警。"""
        for raw in ("0", "-5"):
            monkeypatch.setenv("QODER_BUDGET_SECONDS", raw)
            value, warning = parse_budget(_config(90))
            assert value == 90
            assert warning is not None and "必须为正数" in warning

    def test_over_ceiling_clamped_to_240(self, monkeypatch):
        """超过 240 夹到上限(PT5M 计划任务的 ExecutionTimeLimit 余量)。"""
        monkeypatch.setenv("QODER_BUDGET_SECONDS", "300")
        value, warning = parse_budget(_config(90))
        assert value == BUDGET_CEILING
        assert warning is not None and "240" in warning


class _RecordingCall:
    """替身 httpclient.call: 记录入参,吐预置响应;可断言请求形状。"""

    def __init__(self, resp=(200, {"ok": True}, None)):
        self.resp = resp
        self.calls = []

    def __call__(self, url, *, headers, method, payload, budget, policy, timeout, retry):
        self.calls.append({
            "url": url, "headers": dict(headers), "method": method, "payload": payload,
            "budget": budget, "policy": policy, "timeout": timeout, "retry": retry,
        })
        return self.resp


class TestApiRequestShapes:
    def test_headers_merge_base_risk_and_bearer(self):
        """基底头 + 风控头 + Bearer 组装;基底必含 Cosy-ClientType: 10(缺它列表为空)。"""
        client = Api("tok-1", "https://api.example", risk={"Cosy-MachineId": "mid"})
        headers = client._headers()
        assert headers["Cosy-ClientType"] == "10"
        assert headers["Cosy-MachineId"] == "mid"
        assert headers["Authorization"] == "Bearer tok-1"
        assert headers["Accept"] == "application/json"

    def test_risk_defaults_to_empty_and_base_url_trailing_slash_stripped(self):
        """risk 缺省为空 dict;base_url 结尾斜杠去掉,避免拼接出双斜杠。"""
        client = Api("t", "https://api.example/")
        assert client.risk == {}
        assert client.base_url == "https://api.example"

    def test_campaigns_gets_claimable_url(self, monkeypatch):
        """活动查询: GET CLAIMABLE_URL,无请求体,带统一重试策略与超时。"""
        fake = _RecordingCall()
        monkeypatch.setattr(api_mod, "call", fake)
        budget = Budget(5)
        code, body, err = Api("t", "https://api.example").campaigns(budget=budget)
        assert (code, body, err) == (200, {"ok": True}, None)
        entry = fake.calls[0]
        assert entry["url"] == "https://api.example" + CLAIMABLE_URL
        assert entry["method"] == "GET"
        assert entry["payload"] is None
        assert entry["budget"] is budget
        assert entry["policy"] is RETRY_POLICY
        assert entry["timeout"] == REQUEST_TIMEOUT

    def test_claim_posts_empty_body_without_retry(self, monkeypatch):
        """领取: POST 空体到 /claim;默认不重试防重复,由调用方显式传 retry。"""
        fake = _RecordingCall()
        monkeypatch.setattr(api_mod, "call", fake)
        Api("t", "https://api.example").claim("cmp-7", budget=Budget(5), retry=False)
        entry = fake.calls[0]
        assert entry["url"].endswith("/sash/api/v1/me/campaigns/cmp-7/claim")
        assert entry["method"] == "POST"
        assert entry["payload"] == b""
        assert entry["retry"] is False


class TestSensitiveRegistry:
    def test_mark_token_seen_registers_strings_only(self, monkeypatch):
        """仅非空字符串登记进脱敏表;空串/None/非字符串忽略。"""
        fresh = set()
        monkeypatch.setattr(api_mod, "SENSITIVE", fresh)
        mark_token_seen("tok-1")
        mark_token_seen("")
        mark_token_seen(None)
        mark_token_seen(123)
        assert fresh == {"tok-1"}

    def test_api_keeps_token_expires_at_unset_by_default(self):
        """新建 Api 默认没有 token_expires_at,只在本地登录态路径才填。"""
        assert Api("t", "https://api.example").token_expires_at is None
