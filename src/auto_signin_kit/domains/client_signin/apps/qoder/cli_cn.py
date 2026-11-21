"""qoder_cn 域命令行入口: 与 qoder 同一套实现,换的是客户端身份。

``create_domain_app`` 只取 service 参数的 ``run_action``,这里直接交国内版的域实例
(:data:`service.domain_cn`)——命令形状(auto/status/doctor/--dry-run/--account)
与国际版逐字相同。
"""

from auto_signin_kit.core.kit.cli import create_domain_app
from auto_signin_kit.domains.client_signin.apps.qoder import profile, service
from auto_signin_kit.domains.client_signin.apps.qoder.schema import QoderDomainConfig

app = create_domain_app(
    profile.CN.app_code, profile.CN.display, QoderDomainConfig, service.domain_cn,
    app_help="Qoder 国内版每日 Credits 自动领取",
    auto_help="领取今日 Qoder 国内版 Credits(幂等;重复运行只提示已领取)。",
    dry_run_help="只报告可领取项,不真正领取",
    status_help="只查活动列表,不领取。",
    doctor_help="离线检查登录态可读性与设备组件(不联网、不领取)。",
)
