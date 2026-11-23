"""Qoder 系域命令: 一份实现,两个注册键(qoder=国际版 / qoder_cn=国内版)。"""

from auto_signin_kit.domains.client_signin.apps.qoder.cli import app
from auto_signin_kit.domains.client_signin.apps.qoder.cli_cn import app as app_cn

__all__ = ["app", "app_cn"]
