"""qoder 域命令行入口: 只做参数解析、调 service、异常映射退出码。"""

from __future__ import annotations

from auto_signin_kit.core.kit.cli import create_domain_app
from auto_signin_kit.domains.client_signin.apps.qoder import service
from auto_signin_kit.domains.client_signin.apps.qoder.schema import QoderDomainConfig

app = create_domain_app(
    "qoder", "Qoder CN", QoderDomainConfig, service,
    app_help="Qoder CN 每日 Credits 自动领取",
    auto_help="领取今日 Qoder CN Credits(幂等;重复运行只提示已领取)。",
    dry_run_help="只报告可领取项,不真正领取",
    status_help="只查活动列表,不领取。",
    doctor_help="离线检查登录态可读性与设备组件(不联网、不领取)。",
)
