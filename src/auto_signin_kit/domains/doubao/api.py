"""豆包签到 API 客户端: AGW 网关公共参数 + 签到/详情接口。

请求契约(docs/notes/NOTE-04,2026-11-02 CDP 实测):

- 所有接口都要带**完整公共查询参数**(缺了会报 invalid param/system error):
  ``version_code=20800&language=zh&device_platform=web&doubao_device_platform=web
  &aid=497858&real_aid=497858&pkg_type=release_version``;
- 鉴权完全靠 Cookie(sessionid 等会话 cookie),无 a_bogus/msToken 签名;
- ``POST /alice/ug/campaign/landing/checkin/`` body ``{"scene": <campaign_code>}``
  → 服务端幂等: 已签时回 ``idempotent: true`` + 当日已领信息;
- ``POST /alice/ug/campaign/v3/detail/`` body ``{"campaign_group_code": ...}``
  → 活动详情(任务/案例),status 命令用它看活动是否在期。
"""

from __future__ import annotations

from typing import Any

from auto_signin_kit.core.httpclient import Budget, RetryPolicy, call

#: Web 端 AGW 公共查询参数(2026-11-02 从活动面板真实请求取证;值域常量)。
COMMON_QUERY = (
    "version_code=20800",
    "language=zh",
    "device_platform=web",
    "doubao_device_platform=web",
    "aid=497858",
    "real_aid=497858",
    "pkg_type=release_version",
)
CHECKIN_PATH = "/alice/ug/campaign/landing/checkin/"
DETAIL_PATH = "/alice/ug/campaign/v3/detail/"
LANDING_PATH = "/alice/ug/campaign/landing/get"
CLAIM_PATH = "/alice/ug/campaign/item/reward/claim/"

#: 网络类退避(签到幂等,网络错误可安全重试)。
_RETRY_POLICY = RetryPolicy(network_delays=(0.0, 2.0, 4.0), server_delays=(2.0,))


class DoubaoApi:
    """豆包签到接口客户端;cookie 头由 cookies 模块解出后注入。"""

    def __init__(self, cookie_header: str, *, base_url: str = "https://www.doubao.com",
                 budget: Budget | float | None = None, retry: bool = True) -> None:
        """注入会话 Cookie 头与预算;``retry=False`` 供计划任务的 ``--once`` 语义。

        ``budget`` 接受秒数或 Budget 对象,边界处统一归一化——调用方
        (service.build) 传的是配置里的裸秒数,不能透传给 httpclient.call。
        """
        self.cookie_header = cookie_header
        self.base_url = base_url.rstrip("/")
        self.budget = budget if isinstance(budget, Budget) else Budget(budget or 240.0)
        self.retry = retry

    def _headers(self) -> dict[str, str]:
        return {
            "Cookie": self.cookie_header,
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "Referer": self.base_url + "/pc/agent-explore",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
            ),
        }

    def _post(self, path: str, body: dict[str, Any]) -> tuple[int, Any, str | None]:
        url = "%s%s?%s" % (self.base_url, path, "&".join(COMMON_QUERY))
        return call(url, headers=self._headers(), method="POST", payload=body,
                    budget=self.budget, policy=_RETRY_POLICY, retry=self.retry)

    def checkin(self, campaign_code: str) -> tuple[int, Any, str | None]:
        """签到(幂等);返回 (http_status, 响应json, err)。"""
        return self._post(CHECKIN_PATH, {"scene": campaign_code})

    def campaign_detail(self, campaign_code: str) -> tuple[int, Any, str | None]:
        """活动详情(只读);status 命令用。"""
        return self._post(DETAIL_PATH, {"campaign_group_code": campaign_code})

    def landing_page(self, campaign_code: str) -> tuple[int, Any, str | None]:
        """活动落地页状态(只读;dry-run 用)。"""
        return self._post(LANDING_PATH, {"scene": campaign_code})

    def claim_task(self, campaign_code: str, group_code: str) -> tuple[int, Any, str | None]:
        """领取任务奖励(2026-11-02 实测: task_id 对活动项为空串即可)。

        不可领取时服务端回 ``data.biz_error``(code=3 toast 提示),HTTP 仍 200;
        可领取时无 biz_error,奖励直接入账。
        """
        body = {"task_id": "", "campaign_group_code": group_code,
                "campaign_item_code": campaign_code, "AgwCommonParam": {}}
        return self._post(CLAIM_PATH, body)
