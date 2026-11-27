"""CDP 界面通道: 让豆包客户端自己点签到(把签名交还给客户端)。

2026-11-07 取证结论(排查实录见 docs/notes/NOTE-04 §4.3):

- 活动页前端 SDK 发出的请求已带 ``a_bogus``/``msToken``/``web_id``/``tea_uuid``
  等反爬签名与设备指纹参数,``api.py`` 里"无签名"的旧结论已不成立;
- 裸 HTTP 复刻一旦被服务端要求签名就会失效,而 UI 点击走客户端自己的 JS SDK,
  签名由客户端生成——只要用户能手工签到,这条通道就能签到。
- 现场现象更严重: 反复发裸请求会牵连整个会话,**连客户端手工点都失效、
  重装客户端才恢复**。所以默认通道是"客户端在跑就走界面"。

因此 ``_checkin`` 优先走本通道,再由调用方复查 ``daily_checkin.checked_in_today``
判定真实结果。本模块**不判断业务成功与否**,只负责"把点击送达";

前提: 客户端以 ``--remote-debugging-port=<port>`` 启动;端口不可达时返回
``clicked=False`` 并给出原因,调用方按原错误上报,不做静默降级。

2026-11-27 补: 新版客户端已移除活动页常驻入口,客户端里通常没有活动页。
此时用 CDP ``Target.createTarget`` 自己开一个(借鉴 ai-butler 的
``_activity_conn`` 思路),再找按钮;按钮找不到会等页面渲染后重试,避免
"页面还没加载完就判失败"。
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.request

from auto_signin_kit.domains.client_signin.apps.doubao_cn import cdp

#: 页面里签到按钮的可能文案(精确匹配,避免点到"签到领额度"等标题)。
_BUTTON_TEXTS = ("立即签到", "今日签到", "签到")

#: 活动页(签到面板)地址。2026-11-27 取证: 新版客户端已移除常驻入口,
#: 界面里不再有「免费用豆包」,所以找不到活动页时必须自己开一个。
_ACTIVITY_URL = "https://www.doubao.com/pc/agent-explore"

#: 判断 target 是否活动页的 URL 关键字(与 _pick_target 的优先级一致)。
_ACTIVITY_HINTS = ("campaign", "agent-explore", "incentive")

#: 找按钮 + 滚到视口中央并返回可点击坐标。
_FIND_JS = """
(() => {
  const want = %s;
  const pick = () => [...document.querySelectorAll('*')].find(el =>
      el.children.length <= 2 &&
      want.includes((el.textContent || '').trim()) &&
      getComputedStyle(el).pointerEvents !== 'none');
  let el = pick();
  if (!el) {
    // 按钮可能在视口外: 逐步滚动再找
    const step = Math.round(innerHeight * 0.7);
    for (let pos = 0; pos <= document.body.scrollHeight; pos += step) {
      window.scrollTo(0, pos);
      el = pick();
      if (el) break;
    }
  }
  if (!el) return null;
  el.scrollIntoView({block: 'center'});
  const r = el.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return null;
  return JSON.stringify({x: Math.round(r.x + r.width / 2),
                         y: Math.round(r.y + r.height / 2),
                         text: (el.textContent || '').trim()});
})()
""" % json.dumps(list(_BUTTON_TEXTS))


def _targets(port: int) -> list[dict]:
    # 走 ProxyHandler({}): 本机 127.0.0.1 不该被系统代理(实测开着代理时
    # urllib 会把调试端口请求发给代理,直接连不上)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:%s/json/list" % port, timeout=3) as resp:
        return json.loads(resp.read() or b"[]")


def _pick_target(targets: list[dict]) -> dict | None:
    """优先活动页(URL 含 campaign/agent-explore),其次任一 doubao 页面。"""
    pages = [t for t in targets
             if t.get("type") in ("page", "webview") and "doubao" in (t.get("url") or "")]
    if not pages:
        return None
    for key in ("campaign", "agent-explore", "incentive"):
        for t in pages:
            if key in (t.get("url") or "") and t.get("webSocketDebuggerUrl"):
                return t
    return next((t for t in pages if t.get("webSocketDebuggerUrl")), None)


def _pick_activity(targets: list[dict]) -> dict | None:
    """只认活动页;聊天页/launcher 不算——它们上面没有签到按钮。"""
    pages = [t for t in targets
             if t.get("type") in ("page", "webview") and t.get("webSocketDebuggerUrl")]
    for key in _ACTIVITY_HINTS:
        for t in pages:
            if key in (t.get("url") or ""):
                return t
    return None


def _browser_ws(port: int) -> str | None:
    """取浏览器级 CDP 端点(Target.createTarget 只能经它调用)。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:%s/json/version" % port, timeout=3) as resp:
            return (json.loads(resp.read() or b"{}") or {}).get("webSocketDebuggerUrl")
    except (OSError, ValueError):
        return None


def open_activity_page(port: int, *, wait: float = 10.0) -> dict | None:
    """客户端里没有活动页时,用 CDP 在客户端内新开一个并等它就绪。

    返回页面 target(可直接连 webSocketDebuggerUrl);失败返回 None。
    借鉴 ai-butler 的 ``_activity_conn``: 先复用已有页,没有再 createTarget。
    """
    try:
        import websocket
    except ImportError:
        return None
    browser = _browser_ws(port)
    if not browser:
        return None
    ws = None
    try:
        ws = websocket.create_connection(browser, timeout=10, suppress_origin=True)
        ws.send(json.dumps({"id": 1, "method": "Target.createTarget",
                            "params": {"url": _ACTIVITY_URL}}))
        ws.settimeout(10)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                msg = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            if msg.get("id") == 1:
                break
    except (OSError, ValueError, KeyError):
        return None
    except websocket.WebSocketException:
        return None
    finally:
        if ws is not None:
            with contextlib.suppress(Exception):
                ws.close()
    # 新标签页创建完成≠已可连: 轮询 /json/list 等它出现在目标列表里
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            target = _pick_activity(_targets(port))
        except (OSError, ValueError):
            target = None
        if target and (target.get("url") or "").startswith(_ACTIVITY_URL):
            return target
        time.sleep(0.5)
    return None


def signin_via_ui(port: int | None = None, *, settle: float = 3.0,
                  find_retries: int = 3) -> dict:
    """在客户端页面上做受信任点击签到。

    找不到活动页时自己用 CDP 打开(新版客户端已无常驻入口),因此不再依赖
    用户先手工打开面板。返回 ``{"clicked": bool, "reason": str,
    "button": str|None, "opened": bool}``;是否真的签到成功由调用方复查
    活动详情判定(本函数不臆断结果)。
    """
    try:
        import websocket
    except ImportError:  # 依赖缺失时如实说明,不伪装成"客户端没开"
        return {"clicked": False, "button": None, "opened": False,
                "reason": "缺少 websocket-client 依赖,界面通道不可用"}
    # 端口优先级与 cookies/cdp 一致: 环境变量 > 域配置 > 默认 9222
    port = cdp.resolve_port(port)
    if not cdp.probe(port):
        return {"clicked": False, "button": None, "opened": False,
                "reason": "调试端口 %s 不可达(客户端需带 --remote-debugging-port 启动)" % port}
    try:
        target = _pick_activity(_targets(port))
    except (OSError, ValueError) as exc:
        return {"clicked": False, "button": None, "opened": False,
                "reason": "读取调试端口页面列表失败: %s" % exc}
    opened = False
    if target is None:
        target = open_activity_page(port)
        opened = True
    if not target:
        return {"clicked": False, "button": None, "opened": opened,
                "reason": "客户端里没有活动页,且自动打开 %s 失败" % _ACTIVITY_URL}

    ws = None
    msg_id = 0
    try:
        ws = websocket.create_connection(target["webSocketDebuggerUrl"],
                                        timeout=10, suppress_origin=True)

        def call(method: str, params: dict | None = None, wait: float = 8.0):
            nonlocal msg_id
            msg_id += 1
            ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            deadline = time.time() + wait
            while time.time() < deadline:
                try:
                    msg = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    continue
                if msg.get("id") == msg_id:
                    return msg.get("result") or {}
            return None

        # 活动页可能还在加载/渲染: 找不到按钮就等一会再找,别一次落空就判失败
        box = None
        for attempt in range(max(1, find_retries)):
            found = call("Runtime.evaluate", {"expression": _FIND_JS, "returnByValue": True,
                                              "awaitPromise": True}, wait=20)
            raw = ((found or {}).get("result") or {}).get("value")
            if raw:
                box = json.loads(raw)
                break
            if attempt < find_retries - 1:
                time.sleep(settle)
        if box is None:
            return {"clicked": False, "button": None, "opened": opened,
                    "reason": "页面上找不到签到按钮(可能今日已签,或界面已改版)"}
        x, y = box["x"], box["y"]

        # Input 域派发的是受信任事件: 前端若校验 isTrusted,合成事件会被忽略
        call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y,
                                          "button": "none"})
        time.sleep(0.2)
        call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y,
                                          "button": "left", "clickCount": 1, "buttons": 1})
        time.sleep(0.12)
        call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y,
                                          "button": "left", "clickCount": 1, "buttons": 0})
        time.sleep(settle)  # 等 SDK 把请求发完
        return {"clicked": True, "button": box.get("text"), "opened": opened,
                "reason": "已在客户端界面点击「%s」" % box.get("text")}
    except (OSError, ValueError, KeyError) as exc:
        return {"clicked": False, "button": None, "opened": opened,
                "reason": "界面点击失败: %s" % exc}
    except websocket.WebSocketException as exc:
        return {"clicked": False, "button": None, "opened": opened,
                "reason": "调试会话中断: %s" % exc}
    finally:
        if ws is not None:
            with contextlib.suppress(Exception):  # 关闭失败不影响结果
                ws.close()
