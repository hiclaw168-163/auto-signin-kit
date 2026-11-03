"""core.kit.transport 传输层映射测试(K3 抽取自 trae/qoder if 链)。"""

from __future__ import annotations

from auto_signin_kit.core.httpclient import BUDGET_OUT, NO_NETWORK
from auto_signin_kit.core.kit.transport import short_text, transport_error_dto


def test_short_text_flatten_and_truncate():
    assert short_text("a\n  b\tc") == "a b c"
    assert short_text("abcdef", 5) == "abcde…"
    assert short_text({"b": 1}) == '{"b": 1}'
    assert short_text(None) == ""


def test_http_401_default_report():
    dto = transport_error_dto(401, {}, None)
    assert dto["result"] == "AUTH_REJECTED" and dto["needs_attention"] is True
    assert dto["http"] == 401 and "401" in dto["report"]


def test_http_403_default_report():
    dto = transport_error_dto(403, {}, None)
    assert dto["result"] == "FORBIDDEN" and dto["http"] == 403


def test_custom_auth_report_overrides():
    dto = transport_error_dto(401, {}, None, auth_reports={401: "自定义指引"})
    assert dto["report"] == "自定义指引"


def test_no_network_and_budget_out():
    dto = transport_error_dto(NO_NETWORK, None, "connection refused")
    assert dto["result"] == "NETWORK" and "connection refused" in dto["report"]
    dto = transport_error_dto(BUDGET_OUT, {"error": "超时原因"}, None)
    assert dto["result"] == "TIMEOUT" and dto["report"] == "超时原因"
    dto = transport_error_dto(BUDGET_OUT, None, None)
    assert dto["result"] == "TIMEOUT" and "时间预算" in dto["report"]


def test_business_codes_pass_through():
    # 业务码(200/500/1001 等)不归传输层,返回 None
    assert transport_error_dto(200, {}, None) is None
    assert transport_error_dto(500, {}, None) is None
