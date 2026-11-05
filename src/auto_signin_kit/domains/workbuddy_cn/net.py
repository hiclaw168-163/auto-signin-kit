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

    def __init__(
        self, endpoint: str, headers: dict[str, str], budget: Budget,
        identity: dict[str, str] | None = None,
    ) -> None:
        """凭据/预算在解析完成后就固定,不再中途变。

        ``identity`` 携带 uid/昵称等会话画像(事件上报的指纹字段用),可选;
        缺省为空 dict,只影响行为事件上报,不影响签到/成长接口。
        """
        self._endpoint = endpoint.rstrip("/")
        self._headers = headers
        self._budget = budget
        self.identity: dict[str, str] = dict(identity or {})

    @property
    def endpoint(self) -> str:
        """当前生效的服务端地址。"""
        return self._endpoint

    def budget_remaining(self) -> float:
        """剩余网络请求时间预算(秒);成长中心各步开始前都要重查一次。"""
        return self._budget.remaining()

    def get(self, suffix: str, extra_headers: dict[str, str] | None = None) -> tuple[int, Any]:
        """GET 请求,按 GET_POLICY 退避重试;extra_headers 覆盖同名默认头。"""
        status, body, _err = call(
            self._endpoint + suffix,
            headers=self._merged(extra_headers),
            method="GET",
            budget=self._budget,
            policy=GET_POLICY,
        )
        return status, body

    def post(
        self, suffix: str, payload: Any = None,
        extra_headers: dict[str, str] | None = None, retry: bool = False,
    ) -> tuple[int, Any]:
        """POST 请求;默认不重试——写操作超时若发生在服务端处理之后,重试会重复提交。"""
        return self.post_abs(
            self._endpoint + suffix, payload, extra_headers=extra_headers, retry=retry,
        )

    def post_abs(
        self, url: str, payload: Any = None,
        extra_headers: dict[str, str] | None = None, retry: bool = False,
    ) -> tuple[int, Any]:
        """POST 到任意绝对 URL(web 域事件上报等跨域场景);语义同 :meth:`post`。"""
        status, body, _err = call(
            url,
            headers=self._merged(extra_headers),
            method="POST",
            payload=payload,
            budget=self._budget,
            policy=GET_POLICY,
            retry=retry,
        )
        return status, body

    def _merged(self, extra: dict[str, str] | None) -> dict[str, str]:
        """默认头 + 覆盖头;不改动构造时固定的那份。"""
        if not extra:
            return self._headers
        merged = dict(self._headers)
        merged.update(extra)
        return merged
