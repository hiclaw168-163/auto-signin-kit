"""taskbot 离线测试: 动作表覆盖、过滤规则、指纹注入、专家链与真实对话解析。"""

import pytest

from auto_signin_kit.domains.workbuddy import taskbot
from auto_signin_kit.domains.workbuddy import taskbot_events as ev
from auto_signin_kit.domains.workbuddy.growth import GrowthState

_SSE_ID = "cmb-" + "a" * 32
_SSE_OK = (200, {"raw": 'data: {"id":"%s","object":"chat.id"}\ndata: [DONE]\n' % _SSE_ID})
_EXPERT = {"expert_id": "ex_demo1", "expert_type": "agent", "display_name_zh": "演示专家",
           "profession_zh": "演示", "version": "1.0.0", "categories": ["expert-all"]}


class _Net:
    """taskbot 动作的离线替身: 按序应答并记录调用;不触网。"""

    def __init__(self, gets=(), posts=(), budget=300.0):
        self._gets = list(gets)
        self._posts = list(posts)
        self.calls = []
        self.identity = {"uid": "u-1", "nickname": "测试昵称", "enterprise": ""}
        self.endpoint = "https://copilot.example"
        self._budget = budget

    def budget_remaining(self):
        return self._budget

    def get(self, suffix, extra_headers=None):
        self.calls.append(("GET", suffix, None))
        return self._gets.pop(0) if self._gets else (200, {})

    def post(self, suffix, payload=None, extra_headers=None, retry=False):
        self.calls.append(("POST", suffix, payload))
        return self._posts.pop(0) if self._posts else (200, {})

    def post_abs(self, url, payload=None, extra_headers=None, retry=False):
        self.calls.append(("POST", url, payload))
        return self._posts.pop(0) if self._posts else (200, {})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """动作间的节流与主题等待不真睡,测试保持毫秒级。"""
    monkeypatch.setattr(taskbot.time, "sleep", lambda _s: None)


def test_actions_cover_known_task_codes():
    """当前任务单里的可自动化码必须都有动作;公众号任务显式不做。"""
    assert {"create_canvas", "template_5", "expert_5", "Expert_team_use_3",
            "Expert_lighthouse", "Library_read", "Hp_Appearance", "skill_1",
            "chat_5", "WorkBuddy_Valorant", "expert_use_maidehao",
            "expert_use_douyinyin", "expert_use_shangjia_xuanchuan",
            "expert_use_bowangwang"} <= set(taskbot.ACTIONS)
    assert "wb_wechat_oa_subscribe_task" not in taskbot.ACTIONS


def test_run_progress_filters_and_runs_pending():
    """locked/claimed/completed/达标/无动作全跳过,只对未达标且有动作的任务动手。"""
    tasks = [
        {"task_code": "create_canvas", "title": "画布", "accept_status": "accepted",
         "progress": {"current": 0, "target": 1}},
        {"task_code": "X_locked", "locked": True, "accept_status": "accepted",
         "progress": {"current": 0, "target": 1}},
        {"task_code": "automation_1", "accept_status": "claimed",
         "progress": {"current": 0, "target": 1}},
        {"task_code": "RichMeow_Chat", "accept_status": "completed",
         "progress": {"current": 1, "target": 1}},
        {"task_code": "unknown_task", "accept_status": "accepted",
         "progress": {"current": 0, "target": 1}},
    ]
    net = _Net()
    state = GrowthState()
    taskbot.run_progress(net, state, tasks, net.budget_remaining)
    posts = [p for _m, s, p in net.calls if s == ev.REPORT_SUFFIX and isinstance(p, list)]
    assert len(posts) == 1
    assert "wbx_design_canvas_task_create" in {e.get("eventCode") for e in posts[0]}
    assert state.successes == 1 and state.failures == 0
    assert "任务「画布」" in state.parts[0]


def test_run_progress_stops_when_budget_tight():
    """预算低于单条专家链下限: 记说明并收手,一个请求都不发。"""
    net = _Net(budget=10.0)
    state = GrowthState()
    taskbot.run_progress(net, state, [
        {"task_code": "create_canvas", "accept_status": "accepted",
         "progress": {"current": 0, "target": 1}}],
        net.budget_remaining)
    assert net.calls == []
    assert state.parts == ["时间预算吃紧，剩余任务进度下次再制造"]


def test_act_chat_reports_difference_with_fingerprint():
    """chat_5 差额补报: 2/5 补 3 条,指纹(userId/extName)注入每条事件。"""
    net = _Net()
    msg = taskbot._act_chat(net, {"progress": {"current": 2, "target": 5}})
    assert msg == "已补报 3 条对话活跃事件"
    reports = [p for m, s, p in net.calls if s == ev.REPORT_SUFFIX]
    assert len(reports) == 3
    for batch in reports:
        assert len(batch) == 1
        assert batch[0]["eventCode"] == "chat_request_send"
        assert batch[0]["userId"] == "u-1"
        assert batch[0]["extName"] == "workbuddy-desktop"


def test_report_desktop_business_fields_override_fingerprint():
    """指纹铺底、业务字段覆盖同名键;批量多事件逐条注入。"""
    net = _Net()
    ev.report_desktop(net, [{"eventCode": "a", "userId": "override"},
                            {"eventCode": "b"}])
    batch = net.calls[0][2]
    assert batch[0]["userId"] == "override" and batch[0]["eventCode"] == "a"
    assert batch[1]["userId"] == "u-1" and batch[1]["ideName"] == "WorkBuddy"


def test_report_web_uses_web_channel():
    """Library_read 的 web 事件走 www.workbuddy.cn 绝对 URL + 浏览器指纹。"""
    net = _Net()
    taskbot._act_library(net, {})
    url, payload = net.calls[0][1], net.calls[0][2]
    assert url == ev.WEB_BASE + ev.REPORT_SUFFIX
    assert payload[0]["elementId"] == "library_doc_intro_click"
    assert payload[0]["userId"] == "u-1"


def test_real_chat_parses_server_request_id():
    """真实对话从 SSE 解析服务端 id;解析不到必须报错而不是自造 id 续报。"""
    net = _Net(posts=[_SSE_OK])
    conv, req = ev.real_chat(net, "ex_demo1")
    assert req == _SSE_ID and conv.startswith("wbkit-conv-")
    assert net.calls[0][2]["model"] == ev.CHAT_MODEL
    with pytest.raises(ev.ActionError):
        ev.real_chat(_Net(posts=[(200, {"raw": "data: [DONE]\n"})]))


def test_act_expert_runs_full_chain_per_expert():
    """专家链: 市场列表 → 召唤三连 → 真实对话 → actual_use(带服务端 id),逐个执行。"""
    market = (200, {"data": {"experts": [_EXPERT, dict(_EXPERT, expert_id="ex_d2")]}})
    # 每位专家 3 次 POST(召唤/对话/使用),全部喂 SSE 应答省得按序数消费
    net = _Net(posts=[market] + [_SSE_OK] * 6)
    msg = taskbot.ACTIONS["expert_5"][1](net, {"task_code": "expert_5"})
    assert "2 位" in msg
    kinds = [p[0].get("eventCode") if isinstance(p, list) else "chat"
             for _m, _s, p in net.calls[1:]]
    assert kinds == ["web_element_click", "chat", "agent_task_created"] * 2
    use = net.calls[3][2][-1]  # 使用事件跟在对话链末尾
    assert use["id"] == "ex_demo1" and use["requestId"] == _SSE_ID
    assert use["mode"] == "craft"


def test_find_expert_searches_by_keyword_and_never_settles():
    """指定专家: keyword 搜索→名字命中;全 miss 报错,不拿任意专家凑数。"""
    net = _Net(posts=[(200, {"data": {"experts": [_EXPERT]}})])
    with pytest.raises(ev.ActionError):
        taskbot._find_expert(net, ["无畏契约", "拼豆"])
    hit = _Net(posts=[(200, {"data": {"experts": [
        _EXPERT, dict(_EXPERT, display_name_zh="无畏契约拼豆专家")]}})])
    found = taskbot._find_expert(hit, ["无畏契约", "拼豆"])
    assert found["display_name_zh"] == "无畏契约拼豆专家"
    assert hit.calls[0][2]["keyword"] == "无畏契约"  # 按序用关键词搜索


def test_act_named_expert_runs_chain_with_matched_expert():
    """expert_use_*: 命中指定专家后完成召唤+真实对话+使用链。"""
    net = _Net(posts=[(200, {"data": {"experts": [
        dict(_EXPERT, display_name_zh="商家宣传设计师")]}})] + [_SSE_OK] * 3)
    msg = taskbot.ACTIONS["expert_use_shangjia_xuanchuan"][1](net, {})
    assert "商家宣传设计师" in msg
    kinds = [p[0].get("eventCode") if isinstance(p, list) else "chat"
             for _m, _s, p in net.calls[1:]]
    assert kinds == ["web_element_click", "chat", "agent_task_created"]


def test_act_lighthouse_uses_local_mode_and_empty_type():
    """轻量云专家: actual_use 为 LOCAL、type 空、cost 0(对齐真实样本)。"""
    net = _Net(posts=[(200, {"data": {"experts": []}}), (200, {}), _SSE_OK])
    taskbot._act_lighthouse(net, {})
    use = next(e for _m, _s, p in net.calls if isinstance(p, list)
               for e in p if e.get("eventCode") == "expert_actual_use")
    assert use["mode"] == "LOCAL" and use["type"] == "" and use["cost"] == 0
    assert use["requestId"] == _SSE_ID


def test_act_skill_marks_tool_calls_and_appends_skill_info():
    """skill_1: 对话链的 finishReason 改 tool_calls,末尾追加 skill_info。"""
    net = _Net(posts=[_SSE_OK])
    taskbot._act_skill(net, {})
    last = net.calls[-1][2]
    codes = [e["eventCode"] for e in last]
    assert codes[-1] == "skill_info"
    response = next(e for e in last if e["eventCode"] == "chat_message_response")
    assert response["finishReason"] == "tool_calls"


def test_sequences_carry_expected_event_codes():
    """各组判据序列的事件码齐全(模板/画布/灵感/Buddy 应用)。"""
    assert [e["eventCode"] for e in ev.buddy_app_events("cb_x", "名")] == [
        "buddyapp_discover_click", "buddyapp_show", "buddyapp_enter_click",
        "buddyapp_auth_confirm_click", "buddyapp_bindaccount_skip_click"]
    assert ev.template_group("c", "r", "1", "名")[-1]["eventCode"] == "template_used"
    codes = [e["eventCode"] for e in ev.canvas_events("c", "r")]
    assert "wbx_design_canvas_task_create" in codes and "wbx_design_canvas_open" in codes
    assert ev.playbook_events("c", "r", "id", "名")[-1]["eventCode"] == "playbook_prompt_send"


def test_action_failure_records_state_not_raise():
    """动作失败折算成普通失败(非硬失败),不中断其余任务。"""
    net = _Net(posts=[(200, {"data": {"experts": []}}), (200, {"data": {"experts": []}})])
    state = GrowthState()
    taskbot.run_progress(net, state, [
        {"task_code": "WorkBuddy_Valorant", "title": "无畏契约", "accept_status": "accepted",
         "progress": {"current": 0, "target": 1}},
        {"task_code": "automation_1", "title": "自动化", "accept_status": "accepted",
         "progress": {"current": 0, "target": 1}},
    ], net.budget_remaining)
    assert state.failures == 1 and state.successes == 1
    assert state.hard_failures == 0
