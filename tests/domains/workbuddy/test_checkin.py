"""checkin.py 纯逻辑的离线边界: 认证拒绝、已签判定、汇报组装与 run_auto 编排。"""

from auto_signin_kit.core.httpclient import BUDGET_OUT, NO_NETWORK
from auto_signin_kit.domains.workbuddy import checkin


class _ScriptedNet:
    """按脚本依次应答 POST 并记录路径;run_auto 只用 post,无需真实网络。"""

    def __init__(self, posts):
        self._posts = list(posts)
        self.paths = []

    def post(self, suffix, payload=None, retry=False):
        self.paths.append(suffix)
        return self._posts.pop(0)


def _status_body():
    """状态查询的常规 2xx 形态: 活动开启且今天尚未签到。"""
    return {"active": True, "today_checked_in": False, "streak_days": 1}


def test_auth_failure_dto_distinguishes_401_from_403():
    """401 是认证被拒要提示登录;403 只说明权限/条件不满足,不能当登录过期。"""
    denied = checkin.auth_failure_dto(401)
    assert denied["result"] == "AUTH_REJECTED"
    assert (denied["http"], denied["needs_attention"]) == (401, True)
    assert "401" in denied["report"]
    forbidden = checkin.auth_failure_dto(403)
    assert forbidden["result"] == "FORBIDDEN"
    assert forbidden["needs_attention"] is True
    assert "不能据此判断登录过期" in forbidden["report"]


def test_is_already_checked_in_accepts_all_server_shapes():
    """领取返回表示已签的形态: body 为 None、code 10001、msg 含"已签"都必须识别。"""
    assert checkin.is_already_checked_in(None) is True
    assert checkin.is_already_checked_in({"code": 10001, "msg": "repeat checkin"}) is True
    assert checkin.is_already_checked_in({"code": 0, "msg": "今天已签到"}) is True
    assert checkin.is_already_checked_in({"msg": "你已签过啦"}) is True


def test_is_already_checked_in_rejects_normal_bodies():
    """正常领取返回与陌生结构不能误判成已签,否则会漏掉真实领取。"""
    assert checkin.is_already_checked_in({}) is False
    assert checkin.is_already_checked_in({"code": 0, "msg": "success"}) is False
    assert checkin.is_already_checked_in({"code": 0, "credit": 5}) is False
    assert checkin.is_already_checked_in("已签") is False
    assert checkin.is_already_checked_in(400) is False


def test_already_builds_report_and_falls_back_to_daily_credit():
    """已签汇报用状态字段组装;today_credit 缺失回落 daily_credit;空状态不拼空括号。"""
    full = checkin._already({
        "today_credit": "5", "streak_days": 3, "total_credits": 100,
        "is_streak_day": True, "next_streak_day": 4,
    })
    assert full["result"] == "ALREADY"
    assert full["report"] == "今日已签过（今日 +5，连续 3 天，累计 100 积分）"
    # 字段保留服务端原始值,只有 report 文案做 fmt_credit。
    assert (full["today_credit"], full["streak_days"], full["total_credits"]) == ("5", 3, 100)
    assert (full["is_streak_day"], full["next_streak_day"]) == (True, 4)
    fallback = checkin._already({"daily_credit": 7})
    assert fallback["today_credit"] == 7
    assert "今日 +7" in fallback["report"]
    via = checkin._already({"streak_days": 2}, via="服务端判定已领取")
    assert via["report"] == "服务端判定已领取（连续 2 天）"
    empty = checkin._already({})
    assert empty["report"] == "今日已签过"
    assert empty["streak_days"] is None


def test_reach_failure_maps_budget_and_network_only():
    """预算/网络两类前置失败映射成可重试结果;其余状态码返回 None 表示已送达。"""
    timeout = checkin._reach_failure(BUDGET_OUT, None)
    assert timeout["result"] == "TIMEOUT"
    net_down = checkin._reach_failure(NO_NETWORK, {"error": "DNS 解析失败"})
    assert net_down["result"] == "NETWORK"
    assert net_down["error"] == "DNS 解析失败"
    assert "DNS 解析失败" in net_down["report"]
    assert checkin._reach_failure(NO_NETWORK, None)["error"] == ""
    assert checkin._reach_failure(200, {}) is None
    assert checkin._reach_failure(500, {}) is None


def test_short_json_flattens_truncates_and_falls_back():
    """调试展示: 压扁成单行、超长截断;不可序列化对象回落 repr 而不是抛异常。"""
    assert checkin._short_json({"a": 1}) == '{"a": 1}'
    flat = checkin._short_json({"msg": "第一行\n第二行"})
    assert "\n" not in flat
    cut = checkin._short_json({"k": "x" * 300}, limit=50)
    assert len(cut) == 51 and cut.endswith("…")
    assert checkin._short_json(object()).startswith("<object object")


def test_run_auto_treats_both_already_shapes_as_already():
    """领取返回 code 10001 与 body 为 None 两种形态都要复查状态并汇报 ALREADY。"""
    fresh = {"active": True, "today_checked_in": True, "today_credit": 5, "streak_days": 2}
    for claim in ((400, {"code": 10001}), (200, None)):
        net = _ScriptedNet([(200, _status_body()), claim, (200, fresh)])
        code, out = checkin.run_auto(net)
        assert (code, out["result"]) == (0, "ALREADY")
        assert out["report"].startswith("今日已签过（服务端判定已领取）")
        assert "今日 +5" in out["report"]
        assert net.paths == [
            checkin.STATUS_SUFFIX, checkin.CLAIM_SUFFIX, checkin.STATUS_SUFFIX,
        ]


def test_run_auto_claim_success_reports_fresh_streak():
    """领取成功后必须复查状态,用最新连签/累计数组装汇报,文案不带 None。"""
    fresh = {"active": True, "today_checked_in": True, "streak_days": 2, "total_credits": 120}
    net = _ScriptedNet([(200, _status_body()), (200, {"credit": 5}), (200, fresh)])
    code, out = checkin.run_auto(net)
    assert (code, out["result"]) == (0, "CLAIMED")
    assert out["report"] == "成功领取 5 积分（连续 2 天，累计 120 积分）"
    assert out["credit"] == 5


def test_run_auto_reports_unrecognized_and_error_claim_bodies():
    """带 code/msg 的领取失败要透出原因;完全陌生的返回必须报 UNKNOWN 而非静默。"""
    net = _ScriptedNet([(200, _status_body()), (200, {"code": 5, "msg": "操作太快"})])
    code, out = checkin.run_auto(net)
    assert (code, out["result"]) == (1, "ERROR")
    assert "操作太快" in out["report"] and "HTTP 200" in out["report"]
    odd = _ScriptedNet([(200, _status_body()), (200, {"weird": [1, 2]})])
    code2, out2 = checkin.run_auto(odd)
    assert (code2, out2["result"]) == (1, "UNKNOWN")
    assert '{"weird": [1, 2]}' in out2["report"]
