"""client_signin: 客户端签到引擎域(凭据来自本地客户端的声明式应用)。

接管范围: ``config/apps/*.yaml`` 里 ``kind: client`` 的声明——凭据从
本地客户端解锁(ss_aes 解桌面端存储 / chromium_cookies 解浏览器数据
目录),签到动作是 HTTP 接口;CDP 编排型应用以代码扩展形态挂在
``apps/`` 子包(doubao_cn / workbuddy_cn / qoder)。

机制全部在 core/declarative(工厂 + 策略),本域是薄封装 + 调度声明。
"""

from auto_signin_kit.domains.client_signin.cli import app

__all__ = ["app"]
