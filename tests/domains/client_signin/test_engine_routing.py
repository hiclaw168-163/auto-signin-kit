"""client_signin 引擎路由测试: kind 是引擎的接管边界,走错引擎显式报错。

分类标准 = 凭据来源(见 core/declarative/constants): kind=client(本地
客户端凭据)的声明归 client_signin,kind=web(令牌文件/环境变量)的归
web_signin;声明加载器按 app_code 查,引擎按 kind 过滤并拒绝接管范围外
的声明(带出路,不静默兜底)。
"""

from __future__ import annotations

import pytest

from auto_signin_kit.core.declarative import apps as apps_m
from auto_signin_kit.core.errors import CommandError
from auto_signin_kit.domains.client_signin import service as client_service
from auto_signin_kit.domains.web_signin import service as web_service


def make_decl_of(app_code: str, kind: str):
    from tests.domains.web_signin.test_schema_auth import make_decl

    return make_decl(app_code=app_code, kind=kind)


class TestEngineRouting:
    def test_client_engine_takes_client_decl(self, monkeypatch):
        monkeypatch.setattr(apps_m, "load_app",
                            lambda code: make_decl_of(code, "client"))
        d = client_service.domain_for("trae_cn")
        assert d.name == "trae_cn" and d.display == "Demo"

    def test_web_engine_rejects_client_decl(self, monkeypatch):
        monkeypatch.setattr(apps_m, "load_app",
                            lambda code: make_decl_of(code, "client"))
        with pytest.raises(CommandError, match="client"):
            web_service.domain_for("trae_cn")

    def test_run_action_requires_app_option(self):
        with pytest.raises(CommandError, match="--app"):
            client_service.run_action("claim", None)

    def test_list_lines_only_lists_own_kind(self, monkeypatch):
        from auto_signin_kit.core.kit import actions as actions_m

        decls = [make_decl_of("webby", "web"), make_decl_of("appy", "client")]
        monkeypatch.setattr(apps_m, "all_apps_safe", lambda: (decls, []))
        monkeypatch.setattr(actions_m, "app_enabled", lambda code: True)
        web_lines = "\n".join(web_service.list_lines())
        client_lines = "\n".join(client_service.list_lines())
        assert "webby" in web_lines and "appy" not in web_lines
        assert "appy" in client_lines and "webby" not in client_lines
