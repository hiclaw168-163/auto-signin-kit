"""web_signin 引擎测试(一): schema 校验 / 声明加载 / plain 认证适配器。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from auto_signin_kit.core.declarative import apps as apps_m
from auto_signin_kit.core.declarative import authkit
from auto_signin_kit.core.declarative.schema import AppDeclaration
from auto_signin_kit.core.errors import ConfigError, ConfigMissingError

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


class TestKindAndDesc:
    """应用类别(kind)与描述(desc): 客户端类声明式应用的一等字段。"""

    def test_defaults(self):
        decl = make_decl()
        assert decl.kind == "web" and decl.desc == ""

    def test_client_kind_accepted(self):
        decl = make_decl(kind="client")
        assert decl.kind == "client"

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValidationError, match="kind"):
            make_decl(kind="desktop")

    def test_desc_passthrough(self):
        assert make_decl(desc="客户端每日签到").desc == "客户端每日签到"


class TestCreditsRefreshTime:
    """积分刷新时刻(HH:MM): 判"新一天积分周期"的每应用配置(Qoder 是 10:00)。"""

    def test_default_is_midnight(self):
        assert make_decl().credits_refresh_time == "00:00"

    def test_valid_shape_accepted(self):
        assert make_decl(credits_refresh_time="10:00").credits_refresh_time == "10:00"

    def test_bad_shape_rejected(self):
        for bad in ("25:00", "1000", "9:00", "abc", "10:60"):
            with pytest.raises(ValidationError):
                make_decl(credits_refresh_time=bad)


#%% ---------------- chromium_cookies 适配器 ----------------

import hashlib  # noqa: E402
import sqlite3  # noqa: E402

from auto_signin_kit.core.declarative import chromium_cookies as chromium  # noqa: E402

#: 与 _mac_key 同参数派生的测试密钥(monkeypatch 掉 Keychain 读取)。
_CHROMIUM_KEY = hashlib.pbkdf2_hmac("sha1", b"test-pass", b"saltysalt", 1003,
                                    dklen=16)


def _enc_cookie(value: str, *, key: bytes = _CHROMIUM_KEY) -> bytes:
    """按标准 Chromium os_crypt(mac 分支)造一条加密 cookie 值(测试夹具)。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    data = value.encode("utf-8")
    pad = 16 - len(data) % 16
    enc = Cipher(algorithms.AES(key), modes.CBC(chromium._CBC_IV)).encryptor()
    return b"v10" + enc.update(data + bytes([pad]) * pad) + enc.finalize()


def _make_client_dir(tmp_path, cookies: dict[str, str], *, host: str = ".demo.cn",
                     profile: str = "Default", root_name: str = "client") -> str:
    """一个假客户端数据目录: <root>/<profile>/Cookies sqlite。"""
    root = tmp_path / root_name
    db = root / profile / "Cookies"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("create table cookies (host_key text, name text,"
                     " encrypted_value blob)")
        for name, value in cookies.items():
            conn.execute("insert into cookies values (?, ?, ?)",
                         (host, name, sqlite3.Binary(_enc_cookie(value))))
        conn.commit()
    finally:
        conn.close()
    return str(root)


def _chromium_decl(tmp_path, **auth_overrides) -> AppDeclaration:
    if "files" not in auth_overrides:
        auth_overrides["files"] = [str(tmp_path / "client")]
    return make_decl(kind="client", auth={
        "kind": "chromium_cookies", "files": [],
        "cookie_names": ["sessionid", "uid"], "keychain_service": "Demo Safe Storage",
        **auth_overrides})


class TestChromiumCookies:
    """客户端 Chromium Cookie 库适配器(mac 分支实测;win 分支见 mock 用例)。"""

    def _patch_key(self, monkeypatch):
        monkeypatch.setattr(chromium, "_mac_key", lambda service: _CHROMIUM_KEY)

    def test_resolve_builds_cookie_header(self, tmp_path, monkeypatch):
        self._patch_key(monkeypatch)
        _make_client_dir(tmp_path, {"sessionid": "s-1", "uid": "u-1"})
        decl = _chromium_decl(tmp_path, headers={"X-Static": "1"})
        cred = authkit.resolve(decl, None)
        assert cred.token is None
        assert cred.headers["Cookie"] == "sessionid=s-1; uid=u-1"
        assert cred.headers["X-Static"] == "1"  # auth.headers 静态追加生效
        assert "Cookies" in cred.source

    def test_value_prefix_stripped(self, tmp_path, monkeypatch):
        """应用层值前缀(豆包式 32 字节)可配置剥离。"""
        self._patch_key(monkeypatch)
        root = _make_client_dir(tmp_path, {"sessionid": "s-1", "uid": "u-1"})
        # 重写库: 值前置 32 字节应用层头
        import sqlite3 as sq

        db = next((tmp_path / "client").rglob("Cookies"))
        conn = sq.connect(str(db))
        junk = "x" * 32
        for name, value in (("sessionid", junk + "s-1"), ("uid", junk + "u-1")):
            conn.execute("update cookies set encrypted_value=? where name=?",
                         (sqlite3.Binary(_enc_cookie(value)), name))
        conn.commit()
        conn.close()
        decl = _chromium_decl(root, files=[root], value_prefix_bytes=32)
        assert "sessionid=s-1" in authkit.resolve(decl, None).headers["Cookie"]

    def test_missing_cookie_is_auth_broken(self, tmp_path, monkeypatch):
        self._patch_key(monkeypatch)
        _make_client_dir(tmp_path, {"sessionid": "s-1"})  # 缺 uid
        with pytest.raises(authkit.AuthBrokenError, match="uid"):
            authkit.resolve(_chromium_decl(tmp_path), None)

    def test_absent_dir_is_no_auth(self, tmp_path, monkeypatch):
        self._patch_key(monkeypatch)
        with pytest.raises(authkit.NoAuthError):
            authkit.resolve(_chromium_decl(tmp_path), None)

    def test_stub_declaration_errors_are_explicit(self):
        """新建生成的桩声明(files/cookie_names 空)在运行时显式报错,不静默。"""
        decl = _chromium_decl(None, files=[], keychain_service="")
        with pytest.raises(authkit.NoAuthError, match="files 未声明"):
            authkit.resolve(decl, None)
        assert authkit.ready(decl) is False  # 桩声明就绪探测如实为 False
        out, code = authkit.doctor(decl, None)
        assert out["result"] == "NO_AUTH" and code == 2

    def test_host_domain_filter(self, tmp_path, monkeypatch):
        self._patch_key(monkeypatch)
        _make_client_dir(tmp_path, {"sessionid": "s-1", "uid": "u-1"})
        # host 不含过滤子串: 收不到任何行 → 缺 cookie 报错
        decl = _chromium_decl(tmp_path, cookie_domain="other.com")
        with pytest.raises(authkit.AuthBrokenError):
            authkit.resolve(decl, None)

    def test_ready_and_doctor(self, tmp_path, monkeypatch):
        self._patch_key(monkeypatch)
        decl = _chromium_decl(tmp_path)
        assert authkit.ready(decl) is False
        _make_client_dir(tmp_path, {"sessionid": "s-1", "uid": "u-1"})
        assert authkit.ready(decl) is True
        out, code = authkit.doctor(decl, None)
        assert out["result"] == "OK" and code == 0
        assert set(out["cookies"]) == {"sessionid", "uid"}

    def test_doctor_reports_no_auth(self, tmp_path, monkeypatch):
        self._patch_key(monkeypatch)
        out, code = authkit.doctor(_chromium_decl(tmp_path), None)
        assert out["result"] == "NO_AUTH" and code == 2

    def test_named_account_auth_dir_override(self, tmp_path, monkeypatch):
        """具名账户 auth_dir 显式数据目录(多开客户端各一份)。"""
        self._patch_key(monkeypatch)
        from auto_signin_kit.core.config.schema import AccountRef

        _make_client_dir(tmp_path, {"sessionid": "s-1", "uid": "u-1"})
        _make_client_dir(tmp_path, {"sessionid": "s-2", "uid": "u-2"},
                         root_name="client2")
        decl = _chromium_decl(tmp_path, files=[str(tmp_path / "client")])
        ref = AccountRef(name="second", auth_dir=str(tmp_path / "client2"))
        cookie = authkit.resolve(decl, ref, skip_env=True).headers["Cookie"]
        assert cookie == "sessionid=s-2; uid=u-2"  # 只收显式目录那一份
