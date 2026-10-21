"""fields.py 纯工具的离线边界: 信封取数、数值转换、ETA 换算与响应分类。"""

from auto_signin_kit.core.httpclient import BUDGET_OUT, NO_NETWORK
from auto_signin_kit.domains.workbuddy import fields


def test_dig_unwraps_envelopes_and_misses_safely():
    """dig 兼容 data/result/resp/response 信封与多层嵌套;挖不到一律返回 None。"""
    assert fields.dig({"credit": 5}, "credit") == 5
    assert fields.dig({"data": {"credit": 5}}, "credit") == 5
    assert fields.dig({"result": {"resp": {"credit": 5}}}, "credit") == 5
    assert fields.dig({"credit": None, "data": {"credit": 7}}, "credit") == 7
    assert fields.dig({}, "credit") is None
    assert fields.dig("plain", "credit") is None


def test_as_int_and_fmt_credit_conversion_edges():
    """字符串/浮点安全转 int;Infinity 触发的 OverflowError 必须吃掉并回落默认值。"""
    assert fields.as_int("42") == 42
    assert fields.as_int("1.5") == 1
    assert fields.as_int("1e3") == 1000
    assert fields.as_int(3.9) == 3
    assert fields.as_int(float("inf"), 5) == 5
    assert fields.as_int("abc", 5) == 5
    assert fields.as_int(None, 5) == 5
    assert fields.fmt_credit("12") == 12
    assert fields.fmt_credit("积分") == "积分"
    assert fields.fmt_credit(None) is None


def test_first_int_prefers_response_over_fallback():
    """响应里的实际数值永远优先于活动配置的回落值;两处都没有时归零。"""
    assert fields.first_int({"credit": "9"}, "credit", 3) == 9
    assert fields.first_int({"data": {"credit": 4}}, "credit", 3) == 4
    assert fields.first_int({}, "credit", "6") == 6
    assert fields.first_int({}, "credit") == 0


def test_fmt_eta_boundary_outputs():
    """ETA 换算: 不足一小时按分钟且至少 1 分钟;已到/非法输入给固定文案或空串。"""
    assert fields.fmt_eta(1300, 1200) == "，约 2 分钟后回"
    assert fields.fmt_eta(1230, 1200) == "，约 1 分钟后回"
    assert fields.fmt_eta(1200 + 7200, 1200) == "，约 2.0 小时后回"
    assert fields.fmt_eta(1200, 1300) == "，已到达待领取"
    assert fields.fmt_eta("bad", 1200) == ""
    assert fields.fmt_eta(float("inf"), 1200) == ""
    assert fields.fmt_eta(float("nan"), 1200) == ""


def test_client_token_is_unique_per_call():
    """防重放 token 每次调用都不同,前缀保留。"""
    first = fields.client_token()
    second = fields.client_token("u2")
    assert first != second
    assert second.startswith("u2-")


def test_response_classification_helpers():
    """api_success 要求 2xx 且业务码缺省或整数 0;5xx/无响应是硬失败,伪码翻成人话。"""
    assert fields.api_success(200, {}) is True
    assert fields.api_success(200, {"code": 0}) is True
    assert fields.api_success(200, {"code": "0"}) is False
    assert fields.api_success(200, {"code": 1}) is False
    assert fields.api_success(500, {}) is False
    assert fields.is_hard_failure(500) and fields.is_hard_failure(599)
    assert fields.is_hard_failure(NO_NETWORK) and fields.is_hard_failure(BUDGET_OUT)
    assert not fields.is_hard_failure(400) and not fields.is_hard_failure(200)
    assert fields.http_label(NO_NETWORK) == "网络不可达"
    assert fields.http_label(BUDGET_OUT) == "时间预算耗尽"
    assert fields.http_label(503) == "HTTP 503"
