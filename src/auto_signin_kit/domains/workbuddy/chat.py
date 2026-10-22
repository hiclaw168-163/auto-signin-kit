"""WorkBuddy 活跃保底对话: 自动补一次 /v2/chat/completions。

活跃天数只认对话(rewards.today_activity),检测到当天没有活跃记录时,用签到
同款鉴权头向对话接口发一条最小消息,把"静默断签"变成"自动补上"。

取证(2026-10-22,客户端 app.asar + 实测,见 NOTE-01 §13):

- POST ``{base}/v2/chat/completions``,OpenAI 兼容体 ``{"model","messages","stream"}``
- 只支持流式: ``stream=false`` 回业务码 11101(Non-stream chat request is
  currently not supported)
- ``model`` 缺失回业务码 11102;可用模型经 ``GET /v2/enterprises/personal/models``
  查询,``auto`` 是客户端默认标签(服务端自动路由)
- 鉴权与签到同款(``build_headers`` 的 Bearer + X-User-Id 等),无需额外签名
- 响应为 ``text/event-stream``(SSE): ``data:`` 行携带增量 JSON,聚合
  ``choices[0].delta.content``,以 ``data: [DONE]`` 结束
"""

from __future__ import annotations

import json

from auto_signin_kit.domains.workbuddy.net import Net

#: 活跃保底用的模型标签(客户端默认,服务端自动路由)。
CHAT_MODEL = "auto"
CHAT_SUFFIX = "/v2/chat/completions"
#: 打卡对话的内容: 话越短,消耗的模型额度越少。
CHAT_PROMPT = "这是一次自动活跃打卡，请只回复两个字：好的"


def parse_sse_text(raw: str) -> str:
    """聚合 SSE 文本里所有 ``delta.content``;非流式/异常行一律跳过不抛。"""
    pieces: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            delta = json.loads(payload)["choices"][0].get("delta", {}).get("content")
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            continue
        if delta:
            pieces.append(delta)
    return "".join(pieces)


def run_chat(net: Net) -> dict:
    """发一次保底对话并按一行 JSON 契约返回;失败不抛给上层。

    走 :meth:`Net.post`(写操作默认不重试): 对话不是幂等领取,重发一条消息
    只会多消耗模型额度,失败交给下一次调度。
    """
    if net.budget_remaining() <= 0:
        return {"result": "ERROR", "needs_attention": True,
                "report": "时间预算耗尽，活跃保底对话跳过"}
    status, body = net.post(CHAT_SUFFIX, {
        "model": CHAT_MODEL,
        "messages": [{"role": "user", "content": CHAT_PROMPT}],
        "stream": True,
    })
    if status == 401:
        return {"result": "AUTH_REJECTED", "needs_attention": True,
                "report": "保底对话被拒绝（401），请重新登录客户端"}
    if status == 403:
        return {"result": "FORBIDDEN", "needs_attention": True,
                "report": "保底对话被拒绝（403）"}
    if status != 200:
        code = body.get("code") if isinstance(body, dict) else None
        msg = body.get("msg") if isinstance(body, dict) else None
        return {"result": "ERROR", "needs_attention": True,
                "code": code,
                "report": "保底对话失败：HTTP %s（code=%s，%s）"
                          % (status, code, msg or "无消息")}
    reply = parse_sse_text(body.get("raw", "")) if isinstance(body, dict) else ""
    return {"result": "CHATTED", "needs_attention": False,
            "chat_model": CHAT_MODEL,
            "report": "保底对话已完成" + ("，模型回复：%s" % reply if reply else "")}
