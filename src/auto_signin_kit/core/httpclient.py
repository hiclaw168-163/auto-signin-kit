"""带时间预算与退避重试的 HTTP 客户端(标准库实现)。

各签到域共同的请求层,约定:

- 返回 ``(status, body, err)`` 元组;``status == NO_NETWORK(0)`` 表示"没拿到
  响应"(连不上/超时),``status == BUDGET_OUT(-2)`` 表示"没发出去: 时间预算
  已耗尽"——两个伪状态码沿用签到脚本的成熟设计,调用方
  据此区分失败原因而不是笼统失败。
- 重试按 ``RetryPolicy`` 的退避节奏分桶推进(网络类/服务端 5xx/特定状态码各自
  一份计数),总尝试次数有界,且每次重试前检查预算——宁可现在如实返回失败,
  也不能跑穿定时任务的 ExecutionTimeLimit 被系统强杀(那样连日志都写不出来)。
- 写操作(领取/抽奖等)是否可重试由调用方决定: 只有幂等接口才传可重试策略。
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

#: 没拿到响应: 网络不可达/连接超时。
NO_NETWORK = 0
#: 没发出去: 本次运行的时间预算已耗尽,并主动放弃后续请求。
BUDGET_OUT = -2

#: 单请求超时上限(秒);实际超时还会被剩余预算收缩。
REQUEST_TIMEOUT = 30.0


class Budget:
    """本次运行的网络请求时间预算(单调时钟,不受系统改时间影响)。"""

    def __init__(self, seconds: float) -> None:
        """秒数在启动时固定;运行中改系统时间不影响倒计时。"""
        self._seconds = float(seconds)
        self._start = time.monotonic()

    def remaining(self) -> float:
        """还剩多少秒可用于网络请求。"""
        return self._seconds - (time.monotonic() - self._start)


@dataclass(frozen=True)
class RetryPolicy:
    """退避节奏,按失败类型分桶;桶内各自从头推进自己的退避表。

    分桶的理由(冷启动场景的实测经验): 冷启动场景下前几次是网络不可达,
    之后转成服务端 5xx(代理还没就绪)。若一个全局计数,5xx 会撞上网络类已用光的
    计数、一次都重试不到。
    """

    network_delays: tuple[float, ...] = ()
    server_delays: tuple[float, ...] = ()
    status_delays: dict[int, tuple[float, ...]] = field(default_factory=dict)

    def delays_for(self, status: int) -> tuple[float, ...]:
        """该失败状态对应的退避节奏;空元组表示"重试也没用"。"""
        if status == NO_NETWORK:
            return self.network_delays
        extra = self.status_delays.get(status, ())
        if extra:
            return extra
        if status >= 500:
            return self.server_delays
        return ()


def fetch(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    payload: Any = None,
    timeout: float = REQUEST_TIMEOUT,
) -> tuple[int, Any, str | None]:
    """发一次请求并解析 JSON 响应;任何协议/编码形状异常都不抛出。

    payload 为 bytes/bytearray(如 POST 空体 b"")时按原字节发送,
    dict/list 才走 JSON 序列化。
    """
    if payload is None:
        body = None
    elif isinstance(payload, (bytes, bytearray)):
        body = bytes(payload)
    else:
        try:
            body = json.dumps(payload).encode("utf-8")
        except (TypeError, ValueError):
            return 0, None, "PAYLOAD_NOT_SERIALIZABLE"
    request = urllib.request.Request(
        url, data=body, headers=dict(headers or {}), method=method
    )
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=ssl.create_default_context()
        ) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw), None
            except ValueError:
                return resp.status, {"raw": raw[:500]}, None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw), None
        except ValueError:
            return exc.code, {"raw": raw[:500]}, None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return NO_NETWORK, None, str(reason)
    except OSError as exc:
        # TimeoutError 等都是 OSError 子类,一并归为"网络不可达"。
        return NO_NETWORK, None, str(exc)


def call(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    payload: Any = None,
    budget: Budget,
    policy: RetryPolicy,
    timeout: float = REQUEST_TIMEOUT,
    retry: bool = True,
) -> tuple[int, Any, str | None]:
    """一次带预算守卫与退避重试的调用;超时上限随剩余预算收缩。"""
    if budget.remaining() <= 1:
        return BUDGET_OUT, {"error": "已达本次运行时间预算，跳过剩余请求"}, None
    status, body, err = fetch(
        url, headers=headers, method=method, payload=payload,
        timeout=max(1.0, min(timeout, budget.remaining())),
    )
    if not retry:
        return status, body, err
    # 退避进度按"失败类型"分桶各记一份;总尝试次数有界(各类节奏长度之和 + 1)。
    attempts: dict[tuple[float, ...], int] = {}
    while True:
        delays = policy.delays_for(status)
        if not delays:
            return status, body, err
        used = attempts.get(delays, 0)
        if used >= len(delays):
            return status, body, err
        delay = delays[used]
        attempts[delays] = used + 1
        # 一轮重试最坏要占掉 delay + 一整个超时,预算不够就别开始。
        if budget.remaining() <= delay + timeout:
            return status, body, err
        time.sleep(delay)
        status, body, err = fetch(
            url, headers=headers, method=method, payload=payload,
            timeout=max(1.0, min(timeout, budget.remaining())),
        )
