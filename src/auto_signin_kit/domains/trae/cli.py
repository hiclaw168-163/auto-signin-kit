"""trae 域命令行入口: 只做参数解析、调 service、异常映射退出码。"""

from __future__ import annotations

from auto_signin_kit.core.kit.cli import create_domain_app
from auto_signin_kit.domains.trae import service
from auto_signin_kit.domains.trae.schema import TraeDomainConfig

app = create_domain_app("trae", "Trae CN", TraeDomainConfig, service)
