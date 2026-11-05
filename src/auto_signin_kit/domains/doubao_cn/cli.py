"""doubao 域命令行入口: 只做参数解析、调 service、异常映射退出码。"""

from __future__ import annotations

from auto_signin_kit.core.kit.cli import create_domain_app
from auto_signin_kit.domains.doubao_cn import service
from auto_signin_kit.domains.doubao_cn.schema import DoubaoDomainConfig

app = create_domain_app(
    "doubao_cn", "豆包(电脑版)", DoubaoDomainConfig, service,
    app_help="豆包(电脑版)每日签到领额度",
    auto_help="领取今日豆包签到额度(幂等;重复运行只提示已签到)。",
    dry_run_help="只查活动落地页可达性,不真正签到",
    status_help="查活动是否在期(只读,不签到)。",
    doctor_help="离线检查 Cookie 解密链路与登录态(不联网、不签到)。",
)
