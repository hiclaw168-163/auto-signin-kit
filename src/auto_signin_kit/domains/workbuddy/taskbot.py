"""成长任务进度制造器: 任务码 → 动作映射与执行编排。

动作全部取自 taskbot_events 的实测判据序列(取证口径见该模块 docstring)。
执行顺序即依赖序: 纯上报的便宜动作在前,烧真实对话配额的专家/技能类在后;
单个动作失败不影响其余,预算耗尽把剩余任务留给下一轮轮询。计分是异步的,
本轮制造进度,领奖由下一轮 ``run_tasks`` 收割。

由配置 ``taskbot`` 开关门控(默认关): 专家/技能类动作会消耗真实对话额度。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from auto_signin_kit.domains.workbuddy import taskbot_events as ev
from auto_signin_kit.domains.workbuddy.fields import as_int
from auto_signin_kit.domains.workbuddy.growth import GrowthState, StopRoundError
from auto_signin_kit.domains.workbuddy.net import Net

#: 动作签名: fn(net, task) -> 人话进度描述;失败抛 ActionError(记普通失败)。
Action = Callable[[Net, dict], str]

#: 企鹅教师助手: Buddy_App_QQ 的判据应用,同时满足 Buddy_App「进入任一应用」。
_BUDDY_APP_ID = "cb_y5Dy46tPQGGWtueMxXbe"
_BUDDY_APP_NAME = "企鹅教师助手"
#: 轻量云专家(Expert_lighthouse 判据);市场命中真实条目时以服务端信息为准。
_LIGHTHOUSE_ID = "ex_2cvvUZQhDyeJ"
#: Hp_Appearance 判据主题(和平精英激战金秋)。
_HP_THEME = "theme-tkmw7j"
#: 模板组样本(服务端不校验真实性,5 个不同 id 即可)。
_TEMPLATES = (("1", "深度研究"), ("2", "周报生成"), ("3", "竞品分析"),
              ("4", "活动策划"), ("5", "代码评审"))


def _act_chat(net: Net, task: dict) -> str:
    """chat_5: 按差额补 chat_request_send,条间 REPORT_GAP 防风控。"""
    prog = task.get("progress") or {}
    need = max(as_int(prog.get("target"), 5), 1) - as_int(prog.get("current"))
    if need <= 0:
        return "进度已达标，无需上报"
    for i in range(need):
        suffix = "%d-%d" % (time.time_ns(), i)
        ev.report_desktop(net, [ev.chat_activity_event(
            "wbkit-chat5-" + suffix, "wbkit-chat5-req-" + suffix,
            ev.CHAT_MODEL, ev.CHAT_MODEL)])
        if i < need - 1:
            time.sleep(ev.REPORT_GAP)
    return "已补报 %d 条对话活跃事件" % need


def _act_richmeow(net: Net, task: dict) -> str:
    """RichMeow_Chat: 桌面指纹完整对话事件链,纯上报可点亮。"""
    ms = time.time_ns()
    ev.report_desktop(net, ev.chat_chain("wbkit-rm-%d" % ms, "wbkit-rm-req-%d" % ms,
                                         "req-%d-user" % ms))
    return "已上报桌面对话事件链"


def _act_buddy_app(net: Net, task: dict) -> str:
    ev.report_desktop(net, ev.buddy_app_events(_BUDDY_APP_ID, _BUDDY_APP_NAME))
    return "已上报进入 Buddy 应用五连事件"


def _act_automation(net: Net, task: dict) -> str:
    ev.report_desktop(net, [ev.automation_event("wbkit 自动化")])
    return "已上报定时任务创建事件"


def _act_library(net: Net, task: dict) -> str:
    doc = "https://www.workbuddy.cn/space/d/o0KWYeynteVv06UnAZqIFm"
    ev.report_web(net, "web_element_click", doc, "library_doc_intro_click",
                  "WorkBuddy资料库介绍")
    return "已上报资料库阅读事件"


def _act_template(net: Net, task: dict) -> str:
    for i, (tpl_id, name) in enumerate(_TEMPLATES):
        ms = time.time_ns()
        ev.report_desktop(net, ev.template_group(
            "wbkit-tpl-%d-%d" % (ms, i), "wbkit-tpl-req-%d-%d" % (ms, i), tpl_id, name))
        if i < len(_TEMPLATES) - 1:
            time.sleep(ev.TEMPLATE_GAP)
    return "已上报 template_used ×%d" % len(_TEMPLATES)


def _act_playbook(net: Net, task: dict) -> str:
    ms = time.time_ns()
    ev.report_desktop(net, ev.playbook_events(
        "wbkit-pb-%d" % ms, "wbkit-pb-req-%d" % ms,
        "pm-gtm-launch-plan", "新产品上市 GTM 发布计划一页纸"))
    return "已上报灵感做同款事件组"


def _act_canvas(net: Net, task: dict) -> str:
    ms = time.time_ns()
    ev.report_desktop(net, ev.canvas_events("wbkit-canvas-%d" % ms,
                                            "wbkit-canvas-req-%d" % ms))
    return "已上报设计画布创建事件组"


def _act_appearance(net: Net, task: dict) -> str:
    ev.set_appearance(net, _HP_THEME)
    time.sleep(ev.APPEARANCE_WAIT)
    ev.report_desktop(net, [ev.appearance_apply_event(_HP_THEME)])
    return "已设置主题并上报皮肤生效事件"


def _act_model_chat(net: Net, task: dict) -> str:
    """Model_chat_GLM5.2: 真实对话(指定模型) + 对齐模型字段的活跃上报。"""
    model_id, model_name = "glm-5.2", "GLM-5.2"
    conv, req = ev.real_chat(net, model=model_id)
    suffix = "%d" % time.time_ns()
    ev.report_desktop(net, [ev.chat_activity_event(
        conv or "wbkit-glm-" + suffix, req, model_id, model_name)])
    return "已完成 %s 真实对话并上报" % model_id


def _expert_chain_once(net: Net, expert: dict, mode: str = "craft",
                       has_expert: bool = False) -> None:
    """单个专家的完整「召唤 → 真实对话 → 使用」链(判据要求 id/requestId 真实)。"""
    ev.report_desktop(net, ev.summon_events(expert))
    conv, req = ev.real_chat(net, str(expert.get("expert_id") or ""))
    events = ev.chat_chain(conv, req, "msg-" + req[-8:])
    if has_expert:
        for chain_ev in events:
            if chain_ev.get("eventCode") == "agent_task_created":
                chain_ev.update(has_expert=True, expert_id=expert.get("expert_id"),
                                expert_name=expert.get("display_name_zh"),
                                expert_industry_id="")
    events.append(ev.actual_use_event(expert, conv, req, mode=mode))
    ev.report_desktop(net, events)


def _act_expert(net: Net, task: dict, expert_type: str, count: int) -> str:
    experts = ev.market_experts(net, expert_type)
    if not experts:
        raise ev.ActionError("专家市场列表为空（%s）" % (expert_type or "全部"))
    done = 0
    for expert in experts:
        if done >= count:
            break
        _expert_chain_once(net, expert)
        done += 1
        time.sleep(ev.EXPERT_GAP)
    return "已完成 %d 位真实专家召唤+使用链（%s）" % (done, expert_type)


def _act_lighthouse(net: Net, task: dict) -> str:
    light = {"expert_id": _LIGHTHOUSE_ID, "expert_type": "agent",
             "display_name_zh": "腾讯轻量云专家", "profession_zh": "腾讯轻量云专家",
             "version": "1.0.2", "categories": []}
    for expert in ev.market_experts(net, "agent"):
        if expert.get("expert_id") == _LIGHTHOUSE_ID:
            light = expert
            break
    ev.report_desktop(net, ev.summon_events(light))
    conv, req = ev.real_chat(net, _LIGHTHOUSE_ID)
    events = ev.chat_chain(conv, req, "msg-" + req[-8:])
    for chain_ev in events:
        if chain_ev.get("eventCode") == "agent_task_created":
            chain_ev.update(has_expert=True, expert_id=_LIGHTHOUSE_ID,
                            expert_name=light.get("display_name_zh"), expert_industry_id="")
    use = ev.actual_use_event(light, conv, req, mode="LOCAL")
    use.update(type="", cost=0)  # 对齐真实样本: 轻量云专家 type 为空、cost=0
    ev.report_desktop(net, events + [use])
    return "已上报轻量云专家召唤+使用链（真实对话 requestId）"


def _act_skill(net: Net, task: dict) -> str:
    """skill_1: 真实对话 + finishReason=tool_calls 语义 + skill_info 事件。"""
    conv, req = ev.real_chat(net)
    msg = "msg-" + req[-8:]
    events = ev.chat_chain(conv, req, msg)
    for chain_ev in events:
        if chain_ev.get("eventCode") == "chat_message_response":
            chain_ev["finishReason"] = "tool_calls"
    ev.report_desktop(net, events + [ev.skill_info_event(conv, req, msg)])
    return "已上报真实对话 + skill_info 技能加载事件"


def _act_valorant(net: Net, task: dict) -> str:
    """WorkBuddy_Valorant: 市场里找「无畏契约/拼豆」专家,完成一次使用链。"""
    for expert in ev.market_experts(net, "agent"):
        name = str(expert.get("display_name_zh") or "")
        if "无畏契约" in name or "拼豆" in name:
            _expert_chain_once(net, expert)
            return "已完成「%s」召唤+使用链" % name
    raise ev.ActionError("专家市场未找到无畏契约拼豆专家")


#: 动作表: 顺序即执行顺序(便宜动作在前,烧配额的在后)。
ACTIONS: dict[str, tuple[str, Action]] = {
    "chat_5": ("补报对话活跃事件差额", _act_chat),
    "RichMeow_Chat": ("桌面对话事件链", _act_richmeow),
    "Buddy_App": ("进入 Buddy 应用事件链", _act_buddy_app),
    "Buddy_App_QQ": ("进入 Buddy 应用事件链", _act_buddy_app),
    "automation_1": ("定时任务创建事件", _act_automation),
    "Library_read": ("资料库阅读 web 事件", _act_library),
    "template_5": ("使用模板事件组 ×5", _act_template),
    "playbook_prompt": ("灵感做同款事件组", _act_playbook),
    "create_canvas": ("设计画布创建事件组", _act_canvas),
    "Hp_Appearance": ("设置主题 + 皮肤生效事件", _act_appearance),
    "Model_chat_GLM5.2": ("指定模型真实对话 + 对齐上报", _act_model_chat),
    "skill_1": ("真实对话 + skill_info 事件", _act_skill),
    "WorkBuddy_Valorant": ("无畏契约拼豆专家使用链", _act_valorant),
    "expert_5": ("真实专家召唤+使用链 ×5", lambda n, t: _act_expert(n, t, "agent", 5)),
    "Expert_team_use_3": ("专家团召唤+使用链 ×3", lambda n, t: _act_expert(n, t, "team", 3)),
    "Expert_lighthouse": ("轻量云专家召唤+使用链", _act_lighthouse),
}


def run_progress(
    net: Net, state: GrowthState, tasks: list, budget_left: Callable[[], float],
) -> None:
    """扫描任务列表,对「未领取且未达标且有动作」的任务逐个制造进度。

    单个动作失败记普通失败不中断;401/403 上抛 StopRoundError 结束本轮。
    """
    for task in tasks:
        if not isinstance(task, dict) or task.get("locked"):
            continue
        code = task.get("task_code")
        entry = ACTIONS.get(code)
        if entry is None or task.get("accept_status") in ("claimed", "completed"):
            continue
        prog = task.get("progress") or {}
        target, current = as_int(prog.get("target")), as_int(prog.get("current"))
        if target <= 0 or current >= target:
            continue  # 达标未领的交给 run_tasks 领奖,这里不重复动作
        if budget_left() <= ev.EXPERT_BUDGET_FLOOR:
            state.note("时间预算吃紧，剩余任务进度下次再制造")
            return
        desc, action = entry
        title = task.get("title") or code
        try:
            msg = action(net, task)
            state.ok("任务「%s」%s" % (title, msg))
        except StopRoundError:
            raise
        except ev.ActionError as exc:
            state.fail("任务「%s」自动完成失败：%s" % (title, exc), 400)
        except Exception as exc:  # noqa: BLE001  # 单动作失败不拖垮其余任务
            state.fail("任务「%s」自动完成异常（%s: %s）" % (title, type(exc).__name__, exc), -1)
        time.sleep(ev.REPORT_GAP)
