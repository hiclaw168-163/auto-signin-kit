"""Trae API 客户端与请求层: 端点、令牌装载、预算解析。

契约对齐 Trae CN 桌面端主进程 ttnet 调用(2026-10-21 重新取证):

- ``POST {base}/trae/api/v2/ug/checkin_credits/status`` 与 ``.../claim``
  的 body 均为 ``{"req_source": u}``(正式环境 u=1);
- 鉴权 ``Authorization: Cloud-IDE-JWT <用户JWT>``,额带
  ``x-device-id`` 等设备头——**claim 强制(缺头回下单类业务码 9004),
  status 不校验**;
- ``x-device-id`` 取 storage.json 的 ``telemetry.devDeviceId``,纯明文;
- 业务错误走 HTTP 200 + ``code`` 字段(1001=无法认证,
  9074=活动参与人数过多被限流),先于通用 auth 判定。
"""

from __future__ import annotations

import os
from typing import Any

from auto_signin_kit.core.httpclient import Budget, RetryPolicy, call
from auto_signin_kit.core.logging import result_log_path
from auto_signin_kit.domains.trae.auth import device_meta, resolve_token, token_expired

#: 活动页接口宿主(TRAE_BASE_URL 环境变量优先)。
DEFAULT_BASE_URL = "https://api.trae.cn"
STATUS_URL = "/trae/api/v2/ug/checkin_credits/status"
CLAIM_URL = "/trae/api/v2/ug/checkin_credits/claim"
REQUEST_TIMEOUT = 20.0
#: req_source: 桌面端正式环境发 1(内部构建才发 2);分发部环境无法判定,固定 1。
REQ_SOURCE = 1
#: 查询可重试(网络/5xx);claim 是写操作,默认不重试防重复领取。
RETRY_POLICY = RetryPolicy(
    network_delays=(5, 15, 30),
    server_delays=(5, 15, 30),
)
BUDGET_CEILING = 240.0  # 计划任务 PT5M: 上限 = 300 - 60 收尾余量

#: 登记令牌等敏感值;emit 前统一替换为 [REDACTED]。
SENSITIVE: set[str] = set()


class Api:
    """绑定 token/base_url/设备头的签到请求器。"""

    def __init__(self, token: str, base_url: str, device: dict | None = None) -> None:
        """空 token 只在"已过期"上报路径出现,此刻不发请求。"""
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.token_expires_at: Any = None
        self.device = dict(device or {})

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "Cloud-IDE-JWT %s" % self.token,
        }
        if self.device.get("device_id"):
            headers["x-device-id"] = str(self.device["device_id"])
        app_version = self.device.get("app_version")
        if app_version:
            headers["x-app-version"] = str(app_version)
        return headers

    def call(self, path: str, *, budget: Budget, retry: bool = True) -> tuple[int, Any, str | None]:
        """发一次签到族请求;POST 体固定 {"req_source": 1}。"""
        return call(
            self.base_url + path,
            headers=self._headers(),
            method="POST",
            payload={"req_source": REQ_SOURCE},
            budget=budget,
            policy=RETRY_POLICY,
            timeout=REQUEST_TIMEOUT,
            retry=retry,
        )

    def status(self, *, budget: Budget, retry: bool = True) -> tuple[int, Any, str | None]:
        """查询今日签到状态。"""
        return self.call(STATUS_URL, budget=budget, retry=retry)

    def claim(self, *, budget: Budget) -> tuple[int, Any, str | None]:
        """领取今日签到;服务端幂等,但本域主张重复轮次交给调度器不重试。"""
        return self.call(CLAIM_URL, budget=budget, retry=False)


def log_path():
    """silent 模式日志路径;TRAE_SIGNIN_LOG 覆盖,默认平台日志目录的 trae.log。"""
    return result_log_path("trae", env_var="TRAE_SIGNIN_LOG", filename="trae.log")


def env_flag(name: str) -> bool:
    """开关型环境变量是否为真。"""
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def parse_budget(config: Any) -> tuple[float, str | None]:
    """预算: TRAE_BUDGET_SECONDS 优先;非法回落、越界夹到 240。"""
    default = config.budget_seconds or BUDGET_CEILING
    raw_str = os.environ.get("TRAE_BUDGET_SECONDS")
    if not raw_str:
        return default, None
    try:
        raw = float(raw_str)
    except ValueError:
        return default, "TRAE_BUDGET_SECONDS=%r 不是数字，已回落 %s 秒" % (raw_str, int(default))
    if raw <= 0:
        return default, "TRAE_BUDGET_SECONDS=%s 必须为正数，已回落 %s 秒" % (raw_str, int(default))
    if raw > BUDGET_CEILING:
        return BUDGET_CEILING, ("TRAE_BUDGET_SECONDS=%s 超过 240 秒（须小于定时任务的 "
                                "ExecutionTimeLimit），已夹到 240" % raw)
    return raw, None


def load_session_state(override_dir: str | None = None) -> dict:
    """从本地登录态解出令牌并登记脱敏;异常由 service 收拢。"""
    from auto_signin_kit.domains.trae.auth import load_session

    info = load_session(override_dir)
    token = info.get("token")
    if isinstance(token, str):
        SENSITIVE.add(token)
    return info


def build_api(config: Any) -> tuple[Api, dict | None, str | None]:
    """组 Api: 环境变量令牌优先;令牌过期返回 (api, info, "EXPIRED")。

    设备头取自本机 storage.json(与令牌来源无关: 即使走 TRAE_TOKEN,
    机器 ID 仍在本地,补上才能过 claim 的设备头校验)。
    """
    token, info = resolve_token(config.data_dir)
    if token:
        SENSITIVE.add(token)
    base_url = os.environ.get("TRAE_BASE_URL") or config.base_url
    api = Api(token, base_url, device=device_meta(config.data_dir))
    if info is not None and token_expired(info):
        return api, info, "EXPIRED"
    if info is not None and info.get("expiredAt"):
        api.token_expires_at = info.get("expiredAt")
    return api, info, None
