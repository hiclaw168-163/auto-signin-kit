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


def cookies_via_cdp(port: int | None = None) -> dict[str, str] | None:
    """经 CDP ``Network.getCookies`` 取 doubao.com 的实时 Cookie;不可达返回 None。"""
    port = int(os.environ.get("DOUBAO_CDP_PORT", port or _DEFAULT_PORT))
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%s/json/version" % port, timeout=1) as resp:
            if resp.status != 200:
                return None
    except OSError:
        return None
    try:
        import websocket  # websocket-client,pyproject 声明的域依赖
    except ImportError:
        return None
    pairs: dict[str, str] = {}
    try:
        targets = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%s/json/list" % port, timeout=3).read())
        for target in targets:
            if target.get("type") not in ("page", "webview"):
                continue
            if "doubao" not in (target.get("url") or ""):
                continue
            ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=5)
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
