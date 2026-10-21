"""qoder 域测试: CLI 冒烟 + service 纯函数与领取流程(全离线,不联网)。"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auto_signin_kit.core.httpclient import BUDGET_OUT, NO_NETWORK
from auto_signin_kit.domains.qoder import service
from auto_signin_kit.domains.qoder.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """令牌/数据目录环境变量清空 + 日志隔离,保证用例不依赖真实 Qoder 安装。"""
    for name in ("QODER_TOKEN", "QODER_AUTH_DIR", "QODER_UMID_EXE"):
        monkeypatch.delenv(name, raising=False)
    # 数据目录指到一个空目录: 桌面端"目录在但未登录"的 NO_AUTH 分支。
    monkeypatch.setenv("QODER_AUTH_DIR", str(tmp_path / "empty"))
    monkeypatch.setattr(service, "log_path", lambda: Path(tmp_path) / "qoder.log")


def test_claim_without_auth_reports_no_auth_with_exit_2():
    """无登录态: NO_AUTH + looked_in,退出码 2。"""
    result = runner.invoke(app, ["auto"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["result"] == "NO_AUTH"


def test_doctor_offline_reports_clearly():
    """doctor 离线体检: 不联网也能给出明确的分类结果与探测路径。"""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code in (1, 2)
    payload = json.loads(result.stdout)
    assert payload["result"] in ("NO_AUTH", "AUTH_ERROR")
    assert payload.get("looked_in") or payload.get("token_source")


def test_dry_run_flag_is_accepted(monkeypatch):
    """--dry-run 参数能被正确解析;无登录态时仍应 NO_AUTH(不假装可领)。"""
    result = runner.invoke(app, ["auto", "--dry-run"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["result"] == "NO_AUTH"


# ---------------------------------------------------------------------------
# service 层离线单元测试
# ---------------------------------------------------------------------------


class _NoBudget:
    """预算替身: 永远有剩余,只为满足接口形状。"""

    def remaining(self):
        return 60.0


class _FakeApi:
    """duck-typed Api: campaigns 固定一帧响应;claim 多帧依次吐,单帧则重复使用。"""

    def __init__(self, campaigns=(200, {"campaigns": []}, None), claim=(200, {}, None)):
        self.campaigns_resp = campaigns
        self.claim_seq = claim if isinstance(claim, list) else [claim]
        self.claimed_ids = []
        self.token_expires_at = None

    def campaigns(self, *, budget, retry=True):
        budget.remaining()
        return self.campaigns_resp

    def claim(self, campaign_id, *, budget, retry=True):
        budget.remaining()
        self.claimed_ids.append(campaign_id)
        if len(self.claim_seq) > 1:
            return self.claim_seq.pop(0)
        return self.claim_seq[0]


def _campaign(key="cmp-1", *, claim_status="CLAIMABLE", start=0, end=4102444800000,
              amount=30, action="CLAIM_BENEFIT", validity_days=None):
    """造一个活动行;时间戳毫秒、窗口拉到 2100 年,保证对 time.time() 稳定。"""
    row = {
        "campaignKey": key,
        "campaignId": "id-" + key,
        "actionType": action,
        "claimStatus": claim_status,
        "startAt": start,
        "endAt": end,
    }
    benefit = {}
    if amount is not None:
        benefit["amount"] = amount
    if validity_days is not None:
        benefit["validity"] = {"mode": "RELATIVE_DAYS", "days": validity_days}
    if benefit:
        row["benefit"] = benefit
    return row


class TestServicePureHelpers:
    def test_short_squashes_whitespace_and_truncates(self):
        """调试展示: 压成单行、超长截断并带省略号;dict 走 JSON 压扁。"""
        assert service._short("a\n  b\tc") == "a b c"
        assert service._short("abcdef", 5) == "abcde…"
        assert service._short({"b": 1}) == '{"b": 1}'

    def test_campaigns_ignores_non_dict_rows(self):
        """载荷不是对象/行不是对象时一律丢弃,只留 dict 行。"""
        assert service._campaigns("nope") == []
        assert service._campaigns({"campaigns": [{"a": 1}, "x", 3]}) == [{"a": 1}]
        assert service._campaigns({"campaigns": "nope"}) == []

    def test_benefit_amount_requires_number(self):
        """benefit 缺失或 amount 非数给 None 让调用方回落;0 是合法数额。"""
        assert service._benefit_amount({}) is None
        assert service._benefit_amount({"benefit": {"amount": "30"}}) is None
        assert service._benefit_amount({"benefit": {"amount": 0}}) == 0

    def test_validity_relative_days_only(self):
        """只有 RELATIVE_DAYS 且天数为真值才生成有效期文案。"""
        row = {"benefit": {"validity": {"mode": "RELATIVE_DAYS", "days": 7}}}
        assert service._validity(row) == "领取后 7 天有效"
        row["benefit"]["validity"]["mode"] = "FIXED"
        assert service._validity(row) == ""
        no_days = {"benefit": {"validity": {"mode": "RELATIVE_DAYS"}}}
        assert service._validity(no_days) == ""
        zero_days = {"benefit": {"validity": {"mode": "RELATIVE_DAYS", "days": 0}}}
        assert service._validity(zero_days) == ""

    def test_is_active_window_boundaries(self):
        """窗口判定: start 含端点、end 不含端点;缺字段视为生效。"""
        row = {"startAt": 100, "endAt": 200}
        assert service.is_active(row, 100) is True
        assert service.is_active(row, 199.999) is True
        assert service.is_active(row, 200) is False
        assert service.is_active({"startAt": 100}, 99) is False
        assert service.is_active({"endAt": 200}, 300) is False
        assert service.is_active({}, 12345.0) is True

    def test_fmt_ts_converts_milliseconds(self):
        """秒与毫秒时间戳都转 UTC+8 的 MM-DD HH:MM;非法输入为空串。"""
        assert service._fmt_ts(1735660800) == "01-01 00:00"
        assert service._fmt_ts(1735660800123) == "01-01 00:00"
        assert service._fmt_ts("x") == ""
        assert service._fmt_ts(None) == ""

    def test_window_needs_both_ends(self):
        """窗口展示要求两端都能解析,缺一端给空串。"""
        row = {"startAt": 1735660800, "endAt": 1735747200}
        assert service._window(row) == "01-01 00:00 ~ 01-02 00:00"
        assert service._window({"startAt": 1735660800}) == ""

    def test_campaign_key_fallback_chain(self):
        """campaignKey → campaignId → "?" 的兜底链。"""
        assert service._campaign_key({"campaignKey": "k"}) == "k"
        assert service._campaign_key({"campaignId": 9}) == "9"
        assert service._campaign_key({}) == "?"

    def test_simplify_amount_skips_missing(self):
        """求和跳过缺失项;全缺失给 None 而不是虚报 0。"""
        assert service._simplify_amount([10, None, 5]) == 15
        assert service._simplify_amount([None, None]) is None
        assert service._simplify_amount([]) is None


class TestSelectCampaigns:
    def test_classification_buckets(self):
        """分桶: 非领取类与窗外活动不进任何桶,状态决定 todo/claimed。"""
        todo = _campaign("t", claim_status="CLAIMABLE")
        claimed = _campaign("d", claim_status="CLAIMED")
        pending = _campaign("p", claim_status="PENDING")
        expired = _campaign("e", claim_status="CLAIMABLE", end=500)
        other = _campaign("o", action="SIGN")
        out = service.select_campaigns(
            {"campaigns": [todo, claimed, pending, expired, other, "junk"]}, 1000.0)
        assert out["benefit"] == [todo, claimed, pending, expired]
        assert out["active"] == [todo, claimed, pending]
        assert out["todo"] == [todo]
        assert out["claimed"] == [claimed]

    def test_empty_and_malformed_payload(self):
        """空列表与非法载荷都给出全空分桶,不抛异常。"""
        for payload in (None, [], {}, {"campaigns": []}):
            assert service.select_campaigns(payload, 1000.0) == {
                "benefit": [], "active": [], "todo": [], "claimed": []}


class TestClaimAll:
    def test_success_accumulates_credits_and_keys(self):
        """逐个领取: 累计 Credits、串联 key;响应缺数额时回落活动里的数额。"""
        api = _FakeApi(claim=(200, {"data": {"status": "CLAIMED"}}, None))
        out = service.claim_all(api, [_campaign("a", amount=30), _campaign("b", amount=20)],
                                _NoBudget())
        assert out["result"] == "CLAIMED"
        assert out["credits"] == 50
        assert out["campaign"] == "a, b"
        assert out["needs_attention"] is False
        assert api.claimed_ids == ["id-a", "id-b"]
        assert "成功领取 50 Credits" in out["report"]

    def test_unwrapped_success_body(self):
        """响应没有 data 包裹时按 body 本身判 CLAIMED,不硬性要求包裹层。"""
        api = _FakeApi(claim=(200, {"status": "CLAIMED"}, None))
        out = service.claim_all(api, [_campaign("a")], _NoBudget())
        assert out["result"] == "CLAIMED"
        assert out["credits"] == 30

    def test_validity_report_from_first_campaign(self):
        """有效期文案取第一个活动的 benefit.validity。"""
        first = _campaign("a", amount=30, validity_days=7)
        api = _FakeApi(claim=(200, {"data": {"status": "CLAIMED"}}, None))
        out = service.claim_all(api, [first], _NoBudget())
        assert out["report"] == "成功领取 30 Credits（a），领取后 7 天有效"

    def test_auth_rejected_401_stops_remaining(self):
        """401 立即终止,不再领后面的活动(换凭据才有意义)。"""
        api = _FakeApi(claim=(401, None, None))
        out = service.claim_all(api, [_campaign("a"), _campaign("b")], _NoBudget())
        assert out["result"] == "AUTH_REJECTED"
        assert out["http"] == 401
        assert out["campaign"] == "a"
        assert out["needs_attention"] is True
        assert "重新登录" in out["report"]
        assert api.claimed_ids == ["id-a"]

    def test_forbidden_403_stops_remaining(self):
        """403 同样终止,给账号资格类提示。"""
        api = _FakeApi(claim=(403, None, None))
        out = service.claim_all(api, [_campaign("a"), _campaign("b")], _NoBudget())
        assert out["result"] == "FORBIDDEN"
        assert out["http"] == 403
        assert "资格" in out["report"]
        assert api.claimed_ids == ["id-a"]

    def test_failed_claim_collects_errors_with_partial_success(self):
        """部分成功 + 部分失败 → ERROR 汇总,已领的 key 与 Credits 如实呈现。"""
        api = _FakeApi(claim=[(200, {"data": {"status": "CLAIMED"}}, None),
                              (500, None, "boom")])
        out = service.claim_all(api, [_campaign("ok", amount=10), _campaign("bad")], _NoBudget())
        assert out["result"] == "ERROR"
        assert out["needs_attention"] is True
        assert out["campaign"] == "ok"
        assert out["credits"] == 10
        assert "领取失败" in out["report"] and "bad: HTTP 500 boom" in out["report"]

    def test_unclaimed_status_is_failure(self):
        """HTTP 200 但 status 不是 CLAIMED 也算失败,不虚报成功。"""
        api = _FakeApi(claim=(200, {"data": {"status": "PENDING"}}, None))
        out = service.claim_all(api, [_campaign("a")], _NoBudget())
        assert out["result"] == "ERROR"
        assert "a: HTTP 200" in out["report"]


class TestClaimOnce:
    def test_http_401_and_403_map_to_auth_dto(self):
        """查询阶段 401/403 直接给登录/资格类结论,不打领取请求。"""
        for code, expected in ((401, "AUTH_REJECTED"), (403, "FORBIDDEN")):
            api = _FakeApi(campaigns=(code, None, None))
            out = service.claim_once(api, _NoBudget())
            assert out["result"] == expected
            assert out["http"] == code
            assert out["needs_attention"] is True
            assert api.claimed_ids == []

    def test_network_failure_maps(self):
        """NO_NETWORK(0) 映射 NETWORK 并带错误摘要。"""
        api = _FakeApi(campaigns=(NO_NETWORK, None, "连接超时"))
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "NETWORK"
        assert "连接超时" in out["report"]

    def test_budget_out_maps_with_default_report(self):
        """BUDGET_OUT(-2) 映射 TIMEOUT;payload 无 error 时用默认文案。"""
        api = _FakeApi(campaigns=(BUDGET_OUT, None, None))
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "TIMEOUT"
        assert "时间预算" in out["report"]

    def test_budget_out_uses_server_error_text(self):
        """BUDGET_OUT 载荷带 error 字段时透传服务端文案。"""
        api = _FakeApi(campaigns=(BUDGET_OUT, {"error": "预算耗尽"}, None))
        out = service.claim_once(api, _NoBudget())
        assert out["report"] == "预算耗尽"

    def test_non_200_maps_to_error(self):
        """查询非 200 映射 ERROR,带状态码与响应摘要。"""
        api = _FakeApi(campaigns=(500, None, "boom"))
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "ERROR"
        assert "HTTP 500" in out["report"]

    def test_200_non_dict_payload_maps_to_error(self):
        """200 但载荷不是 JSON 对象也按 ERROR 处理,不硬解。"""
        api = _FakeApi(campaigns=(200, "unexpected", None))
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "ERROR"

    def test_claimable_flows_to_claim(self):
        """有 CLAIMABLE 即领取,并透传 token_expires_at。"""
        api = _FakeApi(campaigns=(200, {"campaigns": [_campaign("a")]}, None),
                       claim=(200, {"data": {"status": "CLAIMED"}}, None))
        api.token_expires_at = "2099-01-01T00:00:00Z"
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "CLAIMED"
        assert out["token_expires_at"] == "2099-01-01T00:00:00Z"

    def test_dry_run_reports_without_posting(self):
        """dry-run 只汇总可领数额与 key,不发领取请求。"""
        api = _FakeApi(campaigns=(200, {"campaigns": [_campaign("a", amount=30),
                                                      _campaign("b", amount=None)]}, None))
        out = service.claim_once(api, _NoBudget(), dry_run=True)
        assert out["result"] == "DRY_RUN"
        assert out["credits"] == 30
        assert out["campaign"] == "a、b"
        assert api.claimed_ids == []

    def test_already_claimed_reports_window(self):
        """窗内已领 → ALREADY,带数额与窗口文案,不需要关注。"""
        row = _campaign("d", claim_status="CLAIMED", amount=50)
        api = _FakeApi(campaigns=(200, {"campaigns": [row]}, None))
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "ALREADY"
        assert out["credits"] == 50
        assert out["needs_attention"] is False
        assert "窗口" in out["report"]

    def test_pending_maps_when_not_claimable(self):
        """活动在窗内但状态不可领(如 PENDING) → PENDING,需人工关注。"""
        row = _campaign("p", claim_status="PENDING")
        api = _FakeApi(campaigns=(200, {"campaigns": [row]}, None))
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "PENDING"
        assert out["needs_attention"] is True
        assert "status=PENDING" in out["report"]

    def test_inactive_campaign_maps_inactive(self):
        """活动已出窗 → INACTIVE,不领取也不需要关注。"""
        row = _campaign("e", end=500)
        api = _FakeApi(campaigns=(200, {"campaigns": [row]}, None))
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "INACTIVE"
        assert out["needs_attention"] is False


class TestRunStatus:
    def test_ok_snapshot_lists_rows(self):
        """状态快照: 每个活动一行,带窗口/有效期/是否在窗内,退出码 0。"""
        row = _campaign("a", claim_status="CLAIMABLE", validity_days=7)
        api = _FakeApi(campaigns=(200, {"campaigns": [row], "showCampaign": True}, None))
        out, code = service.run_status(api, _NoBudget())
        assert code == 0
        assert out["result"] == "OK"
        assert out["report"] == "共 1 个活动"
        assert out["show_campaign"] is True
        line = out["campaigns"][0]
        assert line["campaign"] == "a"
        assert line["status"] == "CLAIMABLE"
        assert line["credits"] == 30
        assert line["active"] is True
        assert line["validity"] == "领取后 7 天有效"
        assert line["window"]

    def test_failure_paths_exit_1(self):
        """查询 401/403/500 都给退出码 1 与对应结论。"""
        for campaigns, expected in (((401, None, None), "AUTH_REJECTED"),
                                    ((403, None, None), "FORBIDDEN"),
                                    ((500, None, "boom"), "ERROR")):
            out, code = service.run_status(_FakeApi(campaigns=campaigns), _NoBudget())
            assert (out["result"], code) == (expected, 1)

    def test_network_failure_exit_1(self):
        """NO_NETWORK → NETWORK,退出码 1。"""
        out, code = service.run_status(_FakeApi(campaigns=(NO_NETWORK, None, "x")), _NoBudget())
        assert out["result"] == "NETWORK"
        assert code == 1
