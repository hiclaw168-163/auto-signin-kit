"""Qoder API 客户端与请求层: HTTP、风控头组装、令牌装载、预算解析。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from auto_signin_kit.core.adapters.platform_info import IS_WINDOWS
from auto_signin_kit.core.httpclient import Budget, RetryPolicy, call
from auto_signin_kit.core.logging import result_log_path
from auto_signin_kit.domains.qoder.auth import resolve_token, token_expired

BASE_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Qoder/claim",
    "Cosy-ClientType": "10",  # 缺这个头,服务端返回空的活动列表
}
REQUEST_TIMEOUT = 20.0
#: 重试只针对网络错误、429 和 5xx;401/403/业务错误重试也不会变好。
RETRY_POLICY = RetryPolicy(
    network_delays=(5, 15, 30),
    server_delays=(5, 15, 30),
    status_delays={429: (5, 15, 30)},
)
CLAIMABLE_URL = "/sash/api/v1/me/campaigns"
BUDGET_CEILING = 240.0  # 计划任务 PT5M: 上限 = 300 - 60 收尾余量

#: 登记令牌等敏感值;emit 前统一替换为 [REDACTED]。
SENSITIVE: set[str] = set()


class Api:
    """绑定 token/base_url/风控头的请求器。"""

    def __init__(self, token: str, base_url: str, risk: dict[str, str] | None = None) -> None:
        """risk 头可选;空 token 只在"已过期"上报路径出现,此刻不发请求。"""
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.risk = risk or {}
        self.token_expires_at: Any = None

    def _headers(self) -> dict[str, str]:
        """固定基底 + 风控头 + Bearer,每次调用现取。"""
        return {**BASE_HEADERS, **self.risk, "Authorization": "Bearer %s" % self.token}

    def call(
        self, path: str, method: str, *, budget: Budget, retry: bool = True
    ) -> tuple[int, Any, str | None]:
        """发一次请求;POST 发空体(领取接口没有请求体)。"""
        code, body, err = call(
            self.base_url + path,
            headers=self._headers(),
            method=method,
            payload=b"" if method == "POST" else None,
            budget=budget,
            policy=RETRY_POLICY,
            timeout=REQUEST_TIMEOUT,
            retry=retry,
        )
        return code, body, err

    def campaigns(self, *, budget: Budget, retry: bool = True) -> tuple[int, Any, str | None]:
        """查询活动列表(缺 Cosy-ClientType 头时服务端返回空列表,见 BASE_HEADERS)。"""
        return self.call(CLAIMABLE_URL, "GET", budget=budget, retry=retry)

    def claim(self, campaign_id: str, *, budget: Budget, retry: bool = True):
        """领取单个活动;重复领取服务端幂等返回 CLAIMED。"""
        return self.call(
            "/sash/api/v1/me/campaigns/%s/claim" % campaign_id, "POST",
            budget=budget, retry=retry,
        )


def log_path() -> Path:
    """silent 模式日志路径;QODER_SIGNIN_LOG 覆盖,默认平台日志目录的 qoder.log。"""
    return result_log_path("qoder", env_var="QODER_SIGNIN_LOG")


def parse_budget(config: Any) -> tuple[float, str | None]:
    """预算: QODER_BUDGET_SECONDS 优先;非法回落、越界夹到 240(PT5M 任务上限)。"""
    default = config.budget_seconds or BUDGET_CEILING
    raw_str = os.environ.get("QODER_BUDGET_SECONDS")
    if not raw_str:
        return default, None
    try:
        raw = float(raw_str)
    except ValueError:
        return (
            default,
            "QODER_BUDGET_SECONDS=%r 不是数字，已回落 %s 秒" % (raw_str, int(default)),
        )
    if raw <= 0:
        return default, "QODER_BUDGET_SECONDS=%s 必须为正数，已回落 %s 秒" % (raw_str, int(default))
    if raw > BUDGET_CEILING:
        return (
            BUDGET_CEILING,
            "QODER_BUDGET_SECONDS=%s 超过 240 秒（须小于定时任务的 ExecutionTimeLimit），已夹到 240"
            % raw,
        )
    return raw, None


def load_session_state() -> dict:
    """从本地登录态解出令牌并登记脱敏;原始异常收拢为 QoderAuthError。"""
    from auto_signin_kit.domains.qoder.auth import load_session

    sess = load_session()
    token = sess.get("token")
    if isinstance(token, str):
        SENSITIVE.add(token)
    return sess


def build_api(config: Any) -> tuple[Api, dict | None, str | None]:
    """组 Api: 令牌(环境变量优先) + 风控头;令牌过期返回 (api, sess, "EXPIRED")。"""
    token, sess = resolve_token()
    if isinstance(token, str):
        SENSITIVE.add(token)
    base_url = os.environ.get("QODER_BASE_URL") or config.base_url
    if sess is not None and token_expired(sess):
        return Api(token, base_url), sess, "EXPIRED"
    from auto_signin_kit.domains.qoder.risk import risk_headers

    risk = risk_headers(sess.get("_auth_dir")) if (IS_WINDOWS and sess) else {}
    api = Api(token, base_url, risk)
    if sess is not None and sess.get("expiresAt"):
        api.token_expires_at = sess.get("expiresAt")
    return api, sess, None


def mark_token_seen(token: Any) -> None:
    """环境变量令牌也登记进脱敏表。"""
    if isinstance(token, str) and token:
        SENSITIVE.add(token)
