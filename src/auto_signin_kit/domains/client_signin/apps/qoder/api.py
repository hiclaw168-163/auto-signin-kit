"""Qoder API 客户端与请求层: HTTP、风控头组装、令牌装载、预算解析。"""

from __future__ import annotations

import os
from typing import Any

from auto_signin_kit.core.httpclient import Budget, RetryPolicy, call
from auto_signin_kit.core.kit.budget import parse_budget as core_parse_budget
from auto_signin_kit.domains.client_signin.apps.qoder.auth import resolve_token, token_expired
from auto_signin_kit.domains.client_signin.apps.qoder.profile import INTL, QoderProfile

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


def parse_budget(config: Any, profile: QoderProfile = INTL) -> tuple[float, str | None]:
    """预算: ``<前缀>_BUDGET_SECONDS`` 优先;非法回落、越界夹到 240(PT5M 任务上限)。"""
    default = config.budget_seconds or BUDGET_CEILING
    return core_parse_budget(
        config.budget_seconds, default, BUDGET_CEILING,
        env_var=profile.env("BUDGET_SECONDS"))


def load_session_state(auth_dir: str | None = None, *, keychain_service: str = "",
                       profile: QoderProfile = INTL) -> dict:
    """从本地登录态解出令牌并登记脱敏;原始异常收拢为 QoderAuthError。

    ``auth_dir``(SPEC-12 P2): 具名账户的显式数据目录,透传给 load_session。
    ``keychain_service``(mac): 该账户的 Keychain 条目名,透传给 load_session。
    ``profile``: 客户端身份(国际版 / 国内版),决定扫哪版数据目录。
    """
    from auto_signin_kit.domains.client_signin.apps.qoder.auth import load_session

    sess = load_session(auth_dir, keychain_service=keychain_service, profile=profile)
    token = sess.get("token")
    if isinstance(token, str):
        SENSITIVE.add(token)
    return sess


def build_api(config: Any, auth_dir: str | None = None, *, keychain_service: str = "",
              profile: QoderProfile = INTL) -> tuple[Api, dict | None, str | None]:
    """组 Api: 令牌(环境变量优先) + 风控头;令牌过期返回 (api, sess, "EXPIRED")。

    API 域优先级: ``<前缀>_BASE_URL`` > 登录态 authenticationContext 推导 >
    配置 base_url——令牌只对签发它的端点有效,登录态推导放配置前面。
    ``auth_dir``(SPEC-12 P2): 具名账户显式数据目录,此时环境变量令牌被隔离。
    ``keychain_service``(mac): 该账户的 Keychain 条目名(两版客户端各一份密钥)。
    ``profile``: 客户端身份——决定令牌变量前缀、目录模式与风控头缓存键。
    """
    token, sess = resolve_token(auth_dir, keychain_service=keychain_service, profile=profile)
    if isinstance(token, str):
        SENSITIVE.add(token)
    from auto_signin_kit.domains.client_signin.apps.qoder.auth import detect_api_base

    base_url = (os.environ.get(profile.env("BASE_URL")) or detect_api_base(sess)
                or config.base_url)
    if sess is not None and token_expired(sess):
        return Api(token, base_url), sess, "EXPIRED"
    from auto_signin_kit.domains.client_signin.apps.qoder.risk import risk_headers

    # 兼容 Windows 与 macOS: 活动接口要求机器身份,两平台都按安装目录取组件。
    risk = risk_headers(sess.get("_auth_dir"), profile) if sess else {}
    api = Api(token, base_url, risk)
    if sess is not None and sess.get("expiresAt"):
        api.token_expires_at = sess.get("expiresAt")
    return api, sess, None


def mark_token_seen(token: Any) -> None:
    """环境变量令牌也登记进脱敏表。"""
    if isinstance(token, str) and token:
        SENSITIVE.add(token)
