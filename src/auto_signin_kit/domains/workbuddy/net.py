"""WorkBuddy API 请求封装: GET 全程重试,POST 默认不重试(写操作防重复提交)。"""

from __future__ import annotations

from typing import Any

from auto_signin_kit.core.httpclient import (
    BUDGET_OUT,
    NO_NETWORK,
    Budget,
    RetryPolicy,
    call,
)

#: 向后兼容的伪状态码名(与 core 常量同值,域内按惯用名引用)。
CODE_NO_NETWORK = NO_NETWORK
CODE_BUDGET_OUT = BUDGET_OUT
REQUEST_TIMEOUT = 30.0

#: GET: 网络类退避到分钟级才能跨过"刚开机网络没就绪"的窗口;5xx 短促重试即可。
GET_POLICY = RetryPolicy(
    network_delays=(5, 15, 30, 60, 90),
    server_delays=(3, 10),
)


class Net:
    """绑定 endpoint/鉴权头/预算的请求器,两个签到接口的调用入口。"""

    def __init__(self, endpoint: str, headers: dict[str, str], budget: Budget) -> None:
        """凭据/预算在解析完成后就固定,不再中途变。"""
        self._endpoint = endpoint.rstrip("/")
        self._headers = headers
        self._budget = budget

    @property
    def endpoint(self) -> str:
        """当前生效的服务端地址。"""
        return self._endpoint

    def budget_remaining(self) -> float:
        """剩余网络请求时间预算(秒);成长中心各步开始前都要重查一次。"""
        return self._budget.remaining()

    def get(self, suffix: str) -> tuple[int, Any]:
        """GET 请求,8 个 GET 类接口共用;按 GET_POLICY 退避重试。"""
        status, body, _err = call(
            self._endpoint + suffix,
            headers=self._headers,
            method="GET",
            budget=self._budget,
            policy=GET_POLICY,
        )
        return status, body

    def post(self, suffix: str, payload: Any = None, retry: bool = False) -> tuple[int, Any]:
        """POST 请求;默认不重试——写操作超时若发生在服务端处理之后,重试会重复提交。"""
        status, body, _err = call(
            self._endpoint + suffix,
            headers=self._headers,
            method="POST",
            payload=payload,
            budget=self._budget,
            policy=GET_POLICY,
            retry=retry,
        )
        return status, body
