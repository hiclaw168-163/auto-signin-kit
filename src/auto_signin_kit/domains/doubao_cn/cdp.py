"""CDP 兜底: 客户端带调试端口运行时,直接取实时解密 Cookie。

文件库读不到/读到旧会话时的兜底通道(见 cookies.py 模块 docstring)。
前提: 豆包客户端以 ``--remote-debugging-port=<port>`` 启动;端口不可达时
返回 None,调用方回落到文件链路的报错。"""

from __future__ import annotations

import json
import os
import urllib.request

_DOUBAO_URL = "https://www.doubao.com"
#: 兜底端口与 cookies.DEFAULT_CDP_PORT 同值;本地定义避免循环导入。
_DEFAULT_PORT = 9222


def resolve_port(fallback: int | None = None) -> int:
    """DOUBAO_CDP_PORT 优先,其次 fallback(域配置 cdp_port),缺省 9222。

    非法值如实抛 ValueError。
    """
    env = os.environ.get("DOUBAO_CDP_PORT")
    if env:
        return int(env)
    return fallback if fallback is not None else _DEFAULT_PORT


def probe(port: int | None = None) -> bool:
    """调试端口可达且返回合法 CDP JSON。

    身份判定**不在**这里做: /json/version 的 Browser 字段是"Chrome/147...",
    新版豆包已不含 doubao 标识(2026-11-03 实测,曾按标识校验导致真豆包被拒);
    真正的身份安全靠 :func:`cookies_via_cdp` 里的 target URL 过滤——
    9222 被其他 Chromium 应用占用时过滤后拿不到任何 doubao cookie,
    返回 None,不会误用别人的会话。
    """
    try:
        port = resolve_port() if port is None else port
        with urllib.request.urlopen(  # noqa: S310 — 仅 127.0.0.1 本机探测
                "http://127.0.0.1:%s/json/version" % port, timeout=1) as resp:
            return resp.status == 200 and bool(json.loads(resp.read()))
    except (OSError, ValueError):
        return False


def cookies_via_cdp(port: int | None = None) -> dict[str, str] | None:
    """经 CDP ``Network.getCookies`` 取 doubao.com 的实时 Cookie;不可达返回 None。"""
    port = resolve_port() if port is None else port
    if not probe(port):
        return None
    try:
        import websocket  # websocket-client,pyproject 声明的域依赖
    except ImportError:
        return None
    pairs: dict[str, str] = {}
    try:
        targets = json.loads(urllib.request.urlopen(  # noqa: S310 — 仅 127.0.0.1 本机探测
            "http://127.0.0.1:%s/json/list" % port, timeout=3).read())
        for target in targets:
            if target.get("type") not in ("page", "webview"):
                continue
            if "doubao" not in (target.get("url") or ""):
                continue
            # suppress_origin 必须带: Chromium 111+ 拒绝带 Origin 头的 CDP 握手
            # (403 Forbidden,提示需要 --remote-allow-origins),websocket-client
            # 默认发 Origin: http://127.0.0.1:<port>,不压掉握手必失败。
            ws = websocket.create_connection(target["webSocketDebuggerUrl"],
                                             timeout=5, suppress_origin=True)
            try:
                ws.send(json.dumps({"id": 1, "method": "Network.getCookies",
                                    "params": {"urls": [_DOUBAO_URL]}}))
                ws.settimeout(5)
                while True:
                    msg = json.loads(ws.recv())
                    if msg.get("id") == 1:
                        for ck in (msg.get("result") or {}).get("cookies") or []:
                            if ck.get("value"):
                                pairs.setdefault(ck["name"], ck["value"])
                        break
            finally:
                ws.close()
            if "sessionid" in pairs:
                break  # 拿到会话即可,不必遍历所有目标
    except (OSError, ValueError, websocket.WebSocketException):
        return pairs or None
    return pairs or None
