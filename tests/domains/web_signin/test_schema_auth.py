"""web_signin 引擎测试(一): schema 校验 / 声明加载 / plain 认证适配器。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from auto_signin_kit.core.errors import ConfigError, ConfigMissingError
from auto_signin_kit.domains.web_signin import apps as apps_m
from auto_signin_kit.domains.web_signin import authkit
from auto_signin_kit.domains.web_signin.schema import AppDeclaration

#: 最小合法声明(plain 认证);测试按需覆盖字段。
BASE_DECL = {
    "app_code": "demo",
    "display": "Demo",
    "base_url": "https://api.demo.cn",
    "auth": {
        "kind": "plain",
        "files": [],
        "token_json_path": "data.token",
        "token_header": {"name": "Authorization", "prefix": "Bearer "},
    },
    "status": {"path": "/status",
               "mapping": {"checked_in": "checked_in", "credits": "credits"}},
    "claim": {"path": "/claim"},
}


def make_decl(**overrides) -> AppDeclaration:
    import copy

    data = copy.deepcopy(BASE_DECL)
    for key, value in overrides.items():
        data[key] = value
    return AppDeclaration.model_validate(data)


@pytest.fixture()
def config_dir(tmp_path, monkeypatch):
    """配置目录换到 tmp_path;apps/ 子目录建好备用。"""
    root = tmp_path / "config"
    (root / "apps").mkdir(parents=True)
    monkeypatch.setattr(apps_m, "resolve_config_dir", lambda: root)
    return root


class TestSchema:
    def test_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            make_decl(nope=1)

    def test_plain_requires_token_json_path(self):
        data = {**BASE_DECL, "auth": {"kind": "plain", "files": []}}
        with pytest.raises(ValidationError, match="token_json_path"):
            AppDeclaration.model_validate(data)

    def test_app_code_pattern(self):
        with pytest.raises(ValidationError):
            make_decl(app_code="BadName")

    def test_business_codes_label_whitelist(self):
        with pytest.raises(ValidationError, match="business_codes"):
            make_decl(business_codes={1001: "WHATEVER"})

    def test_status_mapping_key_whitelist(self):
        with pytest.raises(ValidationError, match="mapping"):
            make_decl(status={"path": "/s", "mapping": {"foo": "bar"}})

    def test_schedule_times_shape(self):
        with pytest.raises(ValidationError, match="times"):
            make_decl(schedule={"times": ["9:5"]})


class TestAppsLoader:
    def write(self, config_dir, name, data):
        import yaml

        path = config_dir / "apps" / name
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return path

    def test_load_app_ok(self, config_dir):
        self.write(config_dir, "demo.yaml", BASE_DECL)
        decl = apps_m.load_app("demo")
        assert decl.app_code == "demo" and decl.region == "cn"

    def test_filename_must_match_app_code(self, config_dir):
        self.write(config_dir, "other.yaml", BASE_DECL)
        with pytest.raises(ConfigError, match="文件名必须与 app_code 相同"):
            apps_m.load_app("other")

    def test_missing_app_lists_available(self, config_dir):
        self.write(config_dir, "demo.yaml", BASE_DECL)
        with pytest.raises(ConfigMissingError, match="可用: demo"):
            apps_m.load_app("ghost")

    def test_broken_yaml_is_strict_in_all_apps(self, config_dir):
        (config_dir / "apps" / "bad.yaml").write_text("app_code: [oops", encoding="utf-8")
        with pytest.raises(ConfigError):
            apps_m.all_apps()

    def test_all_apps_safe_collects_and_warns(self, config_dir, capfd):
        self.write(config_dir, "demo.yaml", BASE_DECL)
        (config_dir / "apps" / "bad.yaml").write_text("app_code: [oops", encoding="utf-8")
        declarations, broken = apps_m.all_apps_safe()
        assert [d.app_code for d in declarations] == ["demo"]
        assert len(broken) == 1 and "bad.yaml" in broken[0]
        assert "警告" in capfd.readouterr().err


class TestPlainAuth:
    def write_creds(self, tmp_path, data, name="creds.json"):
        path = tmp_path / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_resolve_token_and_header(self, tmp_path):
        future = (datetime.now() + timedelta(days=1)).isoformat()
        self.write_creds(tmp_path, {"data": {"token": "tk-1", "exp": future}})
        decl = make_decl(auth={**BASE_DECL["auth"],
                               "files": [str(tmp_path / "creds*.json")],
                               "expires_json_path": "data.exp"})
        cred = authkit.resolve(decl, None)
        assert cred.token == "tk-1"
        assert cred.headers["Authorization"] == "Bearer tk-1"
        assert cred.expires_at == future

    def test_expired_token_rejected(self, tmp_path):
        past = (datetime.now() - timedelta(days=1)).isoformat()
        self.write_creds(tmp_path, {"data": {"token": "tk-1", "exp": past}})
        decl = make_decl(auth={**BASE_DECL["auth"],
                               "files": [str(tmp_path / "creds*.json")],
                               "expires_json_path": "data.exp"})
        with pytest.raises(authkit.AuthRejectedError):
            authkit.resolve(decl, None)

    def test_no_file_is_no_auth(self, tmp_path):
        decl = make_decl(auth={**BASE_DECL["auth"],
                               "files": [str(tmp_path / "absent*.json")]})
        with pytest.raises(authkit.NoAuthError):
            authkit.resolve(decl, None)

    def test_env_token_only_for_default_account(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEMO_TOKEN", "env-tk")
        decl = make_decl(auth={**BASE_DECL["auth"], "files": [],
                               "env": "DEMO_TOKEN"})
        cred = authkit.resolve(decl, None)
        assert cred.token == "env-tk" and cred.source == "env:DEMO_TOKEN"
        # 具名账户隔离环境变量令牌(那是 default 户的)
        from auto_signin_kit.core.config.schema import AccountRef

        with pytest.raises(authkit.AuthError):
            authkit.resolve(decl, AccountRef(name="second"), skip_env=True)

    def test_named_account_auth_file_override(self, tmp_path):
        self.write_creds(tmp_path, {"data": {"token": "tk-second"}},
                         name="second.json")
        from auto_signin_kit.core.config.schema import AccountRef

        decl = make_decl(auth={**BASE_DECL["auth"],
                               "files": [str(tmp_path / "absent*.json")]})
        ref = AccountRef(name="second", auth_file=str(tmp_path / "second.json"))
        assert authkit.resolve(decl, ref, skip_env=True).token == "tk-second"

    def test_doctor_reports_ok_and_no_auth(self, tmp_path):
        self.write_creds(tmp_path, {"data": {"token": "tk-1"}})
        decl = make_decl(auth={**BASE_DECL["auth"],
                               "files": [str(tmp_path / "creds*.json")]})
        out, code = authkit.doctor(decl, None)
        assert out["result"] == "OK" and code == 0
        decl_missing = make_decl(auth={**BASE_DECL["auth"],
                                       "files": [str(tmp_path / "absent*")]})
        out, code = authkit.doctor(decl_missing, None)
        assert out["result"] == "NO_AUTH" and code == 2

    def test_ready_matches_files(self, tmp_path):
        decl = make_decl(auth={**BASE_DECL["auth"],
                               "files": [str(tmp_path / "creds*.json")]})
        assert authkit.ready(decl) is False
        self.write_creds(tmp_path, {"data": {"token": "tk-1"}})
        assert authkit.ready(decl) is True
