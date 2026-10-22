"""成长任务事件基建: 上报通道(桌面/web 指纹)与任务判据事件序列的纯构造。

取证来源: 参考实现 workbuddy2api-panel(autotask.go/desktop.go/report.go,
2026-09 多账号实测) + 本机客户端行为。机制: 任务进度不看 accept/claim 之外
的关键字,而是认 ``POST /v2/report`` 上的行为事件链;不同任务用不同端口形态:

- 桌面事件: 本 endpoint ``/v2/report``,桌面 UA + ``X-Product: SaaS``,
  每个事件注入 extName=workbuddy-desktop 等指纹字段
- web 事件: ``www.workbuddy.cn/v2/report``,浏览器指纹(Library_read 判据)
- 专家/技能类判据 JOIN 的 requestId 必须来自一次**真实对话**返回的服务端
  id(SSE 流里的 32 位 hex/``cmb-`` 前缀),自造 id 不计数;真实对话消耗
  模型额度,故调用方(动作表)由配置开关 ``taskbot`` 门控(默认关)。

计分是异步的: 上报成功 ≠ 进度立即刷新,收割(领奖)交给下一轮轮询的
``run_tasks``。序列只做构造与上报,不解析任务状态——编排见 taskbot.py。
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any

from auto_signin_kit.domains.workbuddy.fields import dig
from auto_signin_kit.domains.workbuddy.growth import check_auth
from auto_signin_kit.domains.workbuddy.net import Net

#: 桌面客户端实测 UA(5.5.6 内嵌 CLI);指纹 os 字段与其配套(实测口径,勿"修正")。
DESKTOP_UA = "WorkBuddy/5.5.6 WorkBuddy/5.5.6 CLI/2.137.1"
IDE_VERSION = "5.5.6"
REPORT_SUFFIX = "/v2/report"
APPEARANCE_SET = "/v2/user-asset/appearance/set"
EXPERT_LIST = "/portal/operation-platform/market/expert/list"
WEB_BASE = "https://www.workbuddy.cn"
CHAT_SUFFIX = "/v2/chat/completions"

#: 连续上报间隔(参考实现实测 1.05s 口径,防风控) / 模板组间隔 / 专家召唤间隔。
REPORT_GAP = 1.05
TEMPLATE_GAP = 0.3
EXPERT_GAP = 6.0
#: 换主题后等事件落盘再上报皮肤生效(参考实现 2s)。
APPEARANCE_WAIT = 2.0
#: 单个专家链(召唤上报+真实对话+使用上报)的预算下限,低于它就留给下一轮。
EXPERT_BUDGET_FLOOR = 20.0

#: SSE 流里服务端 requestId 的形状(cmb- 前缀 32hex 或裸 32hex)。
SERVER_ID_RE = re.compile(r'"id":"((?:cmb-)?[0-9a-f]{32})"')

#: 真实对话固定用的小消息(额度消耗最小化)与默认模型标签。
CHAT_MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "1+1等于几？直接回答。"},
]
CHAT_MODEL = "fast-model"


class ActionError(Exception):
    """动作的可预期失败(上报被拒/拿不到服务端 id 等);记普通失败,不算硬失败。"""


def _derive_id(identity: dict[str, str], salt: str) -> str:
    """由 uid 稳定派生 36 位 hex 设备标识;同账号每次相同,模拟固定设备。"""
    raw = salt + ":" + identity.get("uid", "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:36]


def _fingerprint(identity: dict[str, str]) -> dict[str, Any]:
    """桌面端公共指纹字段(注入每个事件,业务字段可覆盖同名键)。"""
    nickname = identity.get("nickname", "")
    now = int(time.time() * 1000)
    return {
        "timezone": "Asia/Shanghai", "reportDelay": 2000,
        "userId": identity.get("uid", ""), "username": nickname,
        "userNickname": nickname, "product": "SaaS",
        "releaseDate": 1789036585355,
        "commit": "5f9692923c93033111c51ad7b003eb80204a9b75",
        "ideName": "WorkBuddy", "ideType": "WorkBuddy", "ideVersion": IDE_VERSION,
        "machineId": _derive_id(identity, "machine"),
        "sessionId": _derive_id(identity, "session"),
        "extName": "workbuddy-desktop", "extVersion": IDE_VERSION,
        "os": "win32", "arch": "x64", "osVersion": "10.0.26220",
        "cpuCores": 20, "memorySize": 24,
        "timestamp": now, "presentAt": now,
    }


def _desktop_headers(endpoint: str) -> dict[str, str]:
    """桌面端口形态的请求头(UA/X-Domain/X-Product 与默认头不同)。"""
    return {"User-Agent": DESKTOP_UA, "X-Domain": endpoint, "X-Product": "SaaS"}


def _ok(code: int, body: Any, label: str) -> None:
    """非 2xx 统一转 ActionError;401/403 直接上抛 StopRoundError。"""
    check_auth(code)
    if not 200 <= code < 300:
        msg = dig(body, "msg") if isinstance(body, dict) else None
        raise ActionError("%s失败：HTTP %s%s" % (label, code, "（%s）" % msg if msg else ""))


def report_desktop(net: Net, events: list[dict[str, Any]]) -> None:
    """以桌面指纹批量上报事件;指纹先铺底,业务字段覆盖同名键。"""
    if not events:
        raise ActionError("事件列表为空")
    fp = _fingerprint(net.identity)
    payload = [{**fp, **ev} for ev in events]
    code, body = net.post(REPORT_SUFFIX, payload, extra_headers=_desktop_headers(net.endpoint))
    _ok(code, body, "桌面上报(%s 等 %d 事件)" % (events[0].get("eventCode"), len(events)))


def report_web(
    net: Net, event_code: str, page_url: str, element_id: str, element_name: str,
) -> None:
    """以 Web 端指纹向 www.workbuddy.cn/v2/report 上报单事件(页面行为判据)。"""
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
          " (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
    ev = {
        "eventCode": event_code, "timestamp": int(time.time() * 1000), "reportDelay": 0,
        "pageURL": page_url, "elementId": element_id, "elementName": element_name,
        "os": "MacIntel", "arch": "", "osVersion": "10.15.7", "userAgent": ua,
        "machineId": _derive_id(net.identity, "webmachine"),
        "userId": net.identity.get("uid", ""),
        "userNickname": net.identity.get("nickname", ""),
        "enterpriseId": net.identity.get("enterprise", ""),
    }
    headers = {
        "Content-Type": "application/json", "Accept": "application/json",
        "x-client-platform": "web", "Origin": WEB_BASE, "Referer": page_url,
        "User-Agent": ua,
    }
    code, body = net.post_abs(WEB_BASE + REPORT_SUFFIX, [ev], extra_headers=headers)
    _ok(code, body, "web 上报(%s)" % event_code)


def real_chat(net: Net, expert_id: str = "", model: str = CHAT_MODEL) -> tuple[str, str]:
    """发一条真实桌面对话,从 SSE 里解析服务端 requestId;专家任务必须用它。

    返回 (conversation_id, server_request_id);拿不到服务端 id 时抛 ActionError
    (自造 id JOIN 事件不计数,宁可不报也不要白报)。
    """
    conv = "wbkit-conv-%d" % time.time_ns()
    headers = {
        **_desktop_headers(net.endpoint),
        "Accept": "text/event-stream",
        "X-Conversation-ID": conv,
        "X-Request-ID": str(time.time_ns()),
        "X-Agent-Intent": "craft", "X-Agent-Type": "main",
        "X-IDE-Name": "WorkBuddy", "X-IDE-Type": "WorkBuddy",
        "X-IDE-Version": IDE_VERSION, "x-codebuddy-request": "1",
    }
    if expert_id:
        headers["X-Expert-Id"] = expert_id
    code, body = net.post(CHAT_SUFFIX, {
        "model": model, "messages": CHAT_MESSAGES, "agent": "cli",
        "temperature": 1, "stream": True,
        "stream_options": {"include_usage": True},
    }, extra_headers=headers)
    _ok(code, body, "真实对话")
    raw = body.get("raw", "") if isinstance(body, dict) else ""
    found = SERVER_ID_RE.search(raw if isinstance(raw, str) else "")
    if found is None:
        raise ActionError("真实对话完成，但 SSE 中未取到服务端 requestId")
    return conv, found.group(1)


def market_experts(net: Net, expert_type: str) -> list[dict[str, Any]]:
    """拉专家市场真实专家列表(expert_type: agent=单专家 / team=专家团)。

    expert_actual_use 判据要求 id 真实存在,编造 id 不计数,故列表是前置。
    """
    payload: dict[str, Any] = {
        "page": 1, "page_size": 50, "sort_by": "reco_rank", "sort_order": "desc",
    }
    if expert_type:
        payload["expert_type"] = expert_type
    code, body = net.post(EXPERT_LIST, payload, extra_headers=_desktop_headers(net.endpoint))
    _ok(code, body, "专家市场列表")
    experts = dig(body, "experts")
    return [e for e in experts if isinstance(e, dict)] if isinstance(experts, list) else []


def set_appearance(net: Net, resource_key: str) -> None:
    """应用外观主题(留痕);Hp_Appearance 真正判据是随后的皮肤生效事件。"""
    headers = _desktop_headers(net.endpoint)
    code, body = net.post(APPEARANCE_SET, {"kind": "theme", "resource_key": resource_key},
                          extra_headers=headers)
    _ok(code, body, "设置主题")


def _mk(code: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    ev: dict[str, Any] = {"eventCode": code}
    if extra:
        ev.update(extra)
    return ev


def chat_chain(conv: str, req: str, msg: str, model: str = CHAT_MODEL) -> list[dict[str, Any]]:
    """一次「桌面端成功对话」的完整事件链(实测点亮 RichMeow_Chat 等对话判据)。"""
    join = {
        "conversationId": conv, "rootRequestId": req, "parentConversationId": conv,
        "agentName": "cli", "agentType": "main",
        "codebuddy.session_id": conv, "codebuddy.conversation_request_id": req,
    }
    return [
        _mk("agent_task_created", {
            "source": "LOCAL", "name": "working", "task_target": "local", "mode": "craft",
            "requestModelId": model, "requestModelName": model,
            "has_repo": False, "repo_type": "none", "workspace_type": "empty",
            "has_connector": False, "connector_types": [], "has_mention": False,
            "mention_types": [], "has_template": False, "action": "", "template_name": "",
            "has_expert": False, "expert_id": "", "expert_name": "", "expert_industry_id": "",
            "has_skill": False, "skill_names": [], "messageId": msg, "buddyId": "",
            "buddyName": "",
        }),
        _mk("chat_message_send", {
            "messageId": msg + "-assistant", "historyCount": 0,
            "isContextTruncated": False, "currentStepCount": 1,
            "traceId": req, **join,
        }),
        _mk("chat_request_send", {
            "inputLength": 24, "isPlan": False, "isAutoExecuteTerminal": False,
            "isAutoModify": False, "codebaseEnable": False, "maxToken": 0,
            "maxSteps": 500, "temperature": 0, "maxRetries": 0,
            "mentionContexts": [], "knowledgeId": [], "knowledgeName": [],
            "codebaseId": "", "mentionContextCount": 0, "command": "",
            "recommendId": "", "skillId": "", "skillCount": 0, "totalCount": 0,
            "requestModelId": model, "requestModelName": model, "traceId": req, **join,
        }),
        _mk("chat_message_response", {
            "messageId": msg + "-assistant", "responseModelId": model,
            "inputToken": 120, "outputToken": 80, "totalToken": 200,
            "cachedTokens": 0, "cachedWriteTokens": 0, "cachedMissTokens": 0,
            "isSuccessful": True, "messageErrorCode": "", "finishReason": "stop",
            "firstTokenAt": int(time.time() * 1000), "traceId": req, **join,
        }),
        _mk("chat_message_status", {
            "messageId": msg + "-assistant", "messageErrorCode": "0",
            "traceId": req, **join,
        }),
        _mk("chat_request_response", {
            "mode": "craft", "toolCallCount": 0,
            "inputToken": 120, "outputToken": 80, "totalToken": 200,
            "cachedTokens": 0, "cachedWriteTokens": 0, "cachedMissTokens": 0,
            "isSuccessful": True, "messageErrorCode": "", "finishReason": "stop",
            "rootRequestId": req, "parentConversationId": conv,
        }),
    ]


def chat_activity_event(conv: str, req: str, model_id: str, model_name: str) -> dict[str, Any]:
    """单条完整形态的 chat_request_send(chat_5 差额上报的判据事件)。"""
    now = int(time.time() * 1000)
    return {
        "eventCode": "chat_request_send", "timestamp": now, "reportDelay": 0,
        "mode": "craft", "conversationId": conv, "requestId": req,
        "inputLength": 12, "requestModelId": model_id, "requestModelName": model_name,
        "isPlan": False, "isAutoExecuteTerminal": False, "isAutoModify": False,
        "codebaseEnable": False, "maxToken": 0, "maxSteps": 0, "temperature": 0,
        "maxRetries": 0, "mentionContexts": [], "knowledgeId": [], "knowledgeName": [],
        "codebaseId": "", "mentionContextCount": 0, "command": "", "expertId": "",
        "recommendId": "", "skillId": "", "skillCount": 0, "totalCount": 0,
        "fileUri": "", "presentAt": now, "traceId": "", "rootRequestId": req,
        "parentConversationId": conv, "agentName": "default", "agentType": "conversation",
    }


def automation_event(name: str) -> dict[str, Any]:
    """「定时任务创建成功」事件(实测纯上报点亮 automation_1)。"""
    return _mk("automated_task_create_suc", {
        "name": name, "source": "manually", "modelId": CHAT_MODEL,
        "modelIsThinking": True, "connectorCount": 0, "skills": "", "skillCount": 0,
        "scheduleType": "once", "mode": "LOCAL",
    })


def buddy_app_events(buddy_id: str, buddy_name: str) -> list[dict[str, Any]]:
    """「进入 Buddy 应用」五连事件(实测同时点亮 Buddy_App 与 Buddy_App_QQ)。"""

    def mk(code: str, extra: dict[str, Any] | None) -> dict[str, Any]:
        base = {"eventCode": code, "mode": "LOCAL", "buddyId": buddy_id, "buddyName": buddy_name}
        if extra:
            base.update(extra)
        return base

    return [
        mk("buddyapp_discover_click", None),
        mk("buddyapp_show", {"elementId": buddy_id, "elementName": buddy_name, "position": 2}),
        mk("buddyapp_enter_click", {"elementId": buddy_id, "elementName": buddy_name,
                                    "position": 2, "isFirstPage": "1"}),
        mk("buddyapp_auth_confirm_click", {"elementId": buddy_id, "elementName": buddy_name}),
        mk("buddyapp_bindaccount_skip_click", {"elementId": buddy_id, "elementName": buddy_name}),
    ]


def template_group(conv: str, req: str, template_id: str, name: str) -> list[dict[str, Any]]:
    """「使用模板创建任务」事件组(实测 5 组不同模板一次点亮 template_5)。"""
    return chat_chain(conv, req, "msg-" + template_id) + [
        _mk("agent_task_created_with_template", {
            "mode": "working", "isCustomModel": False,
            "id": template_id, "name": name, "requestId": req,
        }),
        _mk("template_used", {"template_id": template_id, "task_mode": "working"}),
    ]


def playbook_events(conv: str, req: str, case_id: str, case_name: str) -> list[dict[str, Any]]:
    """「灵感案例做同款」事件组(判据是 Dialog 发送 playbook_prompt_send)。"""
    payload = {"id": case_id, "name": case_name, "type": "document",
               "categoryId": "", "categoryName": ""}
    return chat_chain(conv, req, "msg-pb") + [
        _mk("web_element_click", {"pageName": "playbook_detail", "elementId": "playbook_ctaClick",
                                  "elementName": case_name, "source": "discover"}),
        _mk("playbook_cta_click", {**payload, "source": "discover", "position": 0}),
        _mk("playbook_prompt_send", {**payload, "conversationId": conv, "requestId": req}),
    ]


def canvas_events(conv: str, req: str) -> list[dict[str, Any]]:
    """「设计创意画布」事件组(实测点亮 create_canvas,+300 分)。"""
    return chat_chain(conv, req, "msg-canvas") + [
        _mk("wbx_design_canvas_task_create", {
            "conversationId": conv, "requestId": req, "source": "summon_keyword",
            "cost": 12000, "isSuccessful": True,
        }),
        _mk("wbx_design_canvas_open", {
            "conversationId": conv, "requestId": req, "id": "ardot-file-" + req[-8:],
            "source": "summon_keyword", "type": "page", "cost": 13000, "isSuccessful": True,
        }),
    ]


def summon_events(expert: dict[str, Any]) -> list[dict[str, Any]]:
    """「召唤平台专家」三连(web_element_click + summon_click + summoned)。"""
    cats = expert.get("categories")
    cat = cats[0] if isinstance(cats, list) and cats and isinstance(cats[0], str) else "expert-all"
    version = expert.get("version") or "1.0.0"
    who = {"id": expert.get("expert_id"), "name": expert.get("display_name_zh"),
           "expertTitle": expert.get("profession_zh")}
    return [
        _mk("web_element_click", {
            "source": expert.get("expert_id"), "type": cat, "version": version,
            "elementId": "expert_summon_click", "elementName": "立即召唤",
            "pageURL": "/C:/Program%20Files/WorkBuddy/resources/app.asar/renderer/index.html",
        }),
        _mk("expert_summon_click", {**who, "type": "expert-all", "position": 0,
                                    "expertType": expert.get("expert_type"),
                                    "version": version, "mode": "LOCAL"}),
        _mk("expert_summoned", {**who, "type": "expert-all"}),
    ]


def actual_use_event(
    expert: dict[str, Any], conv: str, req: str, mode: str = "craft",
) -> dict[str, Any]:
    """expert_actual_use(expert_5/Expert_team_use_3 计数);req 必须是服务端 id。"""
    cats = expert.get("categories")
    cat = cats[0] if isinstance(cats, list) and cats and isinstance(cats[0], str) else "expert-all"
    return _mk("expert_actual_use", {
        "id": expert.get("expert_id"), "name": expert.get("display_name_zh"),
        "expertTitle": expert.get("profession_zh"), "type": cat,
        "expertType": expert.get("expert_type"), "source": "builtin",
        "version": expert.get("version") or "1.0.0",
        "cost": 9000, "characterCount": 14,
        "conversationId": conv, "requestId": req, "messageId": "msg-" + req[-8:],
        "requestModelId": CHAT_MODEL, "requestModelName": CHAT_MODEL,
        "mode": mode,
    })


def appearance_apply_event(resource_key: str) -> dict[str, Any]:
    """皮肤生效事件(Hp_Appearance 真判据: 客户端在主题生效下离开设置页)。"""
    return _mk("appearance_skin_apply", {
        "action": "apply", "source": "settings_close", "id": resource_key,
        "vipLevel": 0, "series": "", "type": "unknown",
    })


def skill_info_event(conv: str, req: str, msg: str) -> dict[str, Any]:
    """skill_info 技能加载事件(skill_1 判据,JOIN 真实会话)。"""
    return _mk("skill_info", {
        "id": "润泽小馆·日报撰写", "skillId": "skill_2097350077599879168",
        "skillVersion": "1.0.0", "toolStatus": "success", "fileCount": 56,
        "source": "workbuddy-desktop",
        "conversationId": conv, "requestId": req, "messageId": msg,
        "requestModelId": CHAT_MODEL, "requestModelName": CHAT_MODEL, "traceId": req,
    })
