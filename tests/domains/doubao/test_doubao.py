"""doubao 域测试: cookie 解密链路、API 契约、service 动作映射(全 mock,不碰真实客户端)。"""

from __future__ import annotations

import base64
import json
import sqlite3

import pytest
from typer.testing import CliRunner

from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.domains.doubao import cookies as cookies_mod
from auto_signin_kit.domains.doubao import service
from auto_signin_kit.domains.doubao.api import COMMON_QUERY, DoubaoApi
from auto_signin_kit.domains.doubao.cli import app
from auto_signin_kit.domains.doubao.schema import DoubaoDomainConfig

PREFIX = b"P" * cookies_mod.APP_LAYER_VALUE_PREFIX
FAKE_KEY = b"K" * 32


@pytest.fixture
def fake_crypto(monkeypatch):
    monkeypatch.setattr(cookies_mod, 'cookies_via_cdp', lambda port=None: None)
    """替换 DPAPI/GCM: unprotect 返回固定密钥,decrypt_gcm 返回 前缀+明文。"""
    monkeypatch.setattr(cookies_mod, "unprotect", lambda data: FAKE_KEY)
    monkeypatch.setattr(cookies_mod, "decrypt_gcm", lambda key, nonce, blob: PREFIX + blob)


def _make_user_data(tmp_path, rows):
    """构造假豆包数据目录: Local State + Cookies 库(明文值在 fake_crypto 下解出)。"""
    root = tmp_path / "User Data"
    root.mkdir(parents=True)
    (root / "Local State").write_text(json.dumps(
        {"os_crypt": {"encrypted_key": base64.b64encode(b"DPAPI" + b"x" * 32).decode()}}),
        encoding="utf-8")
    db = root / "Default" / "Network" / "Cookies"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db))
    con.execute("create table cookies (host_key text, name text, encrypted_value blob)")
    for host, name, value in rows:
        # 库里存 v10 前缀+nonce+密文;fake_crypto 下 decrypt_gcm 把 blob 还原成
        # 前缀+明文,所以这里直接把"明文"当 blob 存
        con.execute("insert into cookies values (?,?,?)",
                    (host, name, b"v10" + b"N" * 12 + value.encode()))
    con.commit()
    con.close()
    return root


class TestCookieDecrypt:
    def test_load_cookie_header_strips_prefix_and_builds_header(self, tmp_path, fake_crypto):
        root = _make_user_data(tmp_path, [
            (".doubao.com", "sessionid", "9c18d6b0ef1eb14bace5f4d4a058c419"),
            (".doubao.com", "sid_tt", "9c18d6b0ef1eb14bace5f4d4a058c419"),
            (".doubao.com", "uid_tt", "3cbc83281d06e2416707e916919e0d1d"),
        ])
        header = cookies_mod.load_cookie_header(root)
        assert "sessionid=9c18d6b0ef1eb14bace5f4d4a058c419" in header
        assert header.count("=") == 3

    def test_missing_required_cookie_raises_no_auth(self, tmp_path, fake_crypto):
        root = _make_user_data(tmp_path, [(".doubao.com", "sid_tt", "abc")])
        with pytest.raises(cookies_mod.DoubaoCookieError) as exc:
            cookies_mod.load_cookie_header(root)
        assert "sessionid" in str(exc.value)

    def test_dot_host_cookie_wins_over_www(self, tmp_path, fake_crypto):
        root = _make_user_data(tmp_path, [
            ("www.doubao.com", "sessionid", "from-www"),
            (".doubao.com", "sessionid", "from-dot"),
            (".doubao.com", "sid_tt", "x"),
            (".doubao.com", "uid_tt", "y"),
        ])
        assert "sessionid=from-dot" in cookies_mod.load_cookie_header(root)

    def test_undecryptable_rows_are_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cookies_mod, "unprotect", lambda data: FAKE_KEY)
        monkeypatch.setattr(cookies_mod, "cookies_via_cdp", lambda port=None: None)

        def boom(key, nonce, blob):
            raise cookies_mod.DecryptError("bad tag")

        monkeypatch.setattr(cookies_mod, "decrypt_gcm", boom)
        root = _make_user_data(tmp_path, [(".doubao.com", "sessionid", "whatever")])
        with pytest.raises(AutoSignInKitError):
            cookies_mod.load_cookie_header(root)

    def test_missing_cookies_db_reports_clearly(self, tmp_path):
        root = tmp_path / "empty"
        root.mkdir()
        with pytest.raises(cookies_mod.DoubaoCookieError) as exc:
            cookies_mod.load_cookie_header(root)
        assert "Cookie 库不存在" in str(exc.value)


class TestDoubaoApi:
    def test_urls_carry_full_common_query(self, monkeypatch):
        captured = {}

        def fake_call(url, **kwargs):
            captured["url"] = url
            return 200, {"code": 0, "data": {}}, None

        monkeypatch.setattr("auto_signin_kit.domains.doubao.api.call", fake_call)
        DoubaoApi("sessionid=x").checkin("pc_incentive_task_mvp_v3")
        assert "/alice/ug/campaign/landing/checkin/" in captured["url"]
        for part in COMMON_QUERY:  # 公共参数缺一不可(取证: 缺了报 system error)
            assert part in captured["url"]

    def test_checkin_body_uses_scene(self, monkeypatch):
        captured = {}

        def fake_call(url, **kwargs):
            captured["payload"] = kwargs.get("payload")
            return 200, {"code": 0, "data": {}}, None

        monkeypatch.setattr("auto_signin_kit.domains.doubao.api.call", fake_call)
        DoubaoApi("sessionid=x").checkin("pc_incentive_task_mvp_v3")
        assert captured["payload"] == {"scene": "pc_incentive_task_mvp_v3"}

    def test_once_disables_retry(self, monkeypatch):
        captured = {}

        def fake_call(url, **kwargs):
            captured["retry"] = kwargs.get("retry")
            return 200, {"code": 0, "data": {}}, None

        monkeypatch.setattr("auto_signin_kit.domains.doubao.api.call", fake_call)
        DoubaoApi("sessionid=x", retry=False).checkin("s")
        assert captured["retry"] is False


def _config(tmp_path):
    return DoubaoDomainConfig(campaign_code="pc_incentive_task_mvp_v3",
                              user_data_dir=str(tmp_path), budget_seconds=60)


@pytest.fixture
def no_cookies(monkeypatch):
    """service 层测试: cookie 链路直接返回假头,绕开文件系统。"""
    monkeypatch.setattr(cookies_mod, "load_cookie_header",
                        lambda user_data_dir=None: "sessionid=fake")


class TestServiceActions:
    def test_claim_already_is_zero_and_info(self, monkeypatch, no_cookies):
        monkeypatch.setattr("auto_signin_kit.domains.doubao.api.call", lambda *a, **k: (
            200, {"code": 0, "data": {"checked_in": True, "idempotent": True,
                                      "reward_amount": 400, "continuous_days": 1,
                                      "checkin_date": "2026-11-02"}}, None))
        assert service.run_action("claim", _config(no_cookies), command="auto") == 0

    def test_claim_fresh_returns_claimed(self, monkeypatch, no_cookies):
        monkeypatch.setattr("auto_signin_kit.domains.doubao.api.call", lambda *a, **k: (
            200, {"code": 0, "data": {"checked_in": True, "idempotent": False,
                                      "reward_amount": 200, "continuous_days": 1}}, None))
        assert service.run_action("claim", _config(no_cookies)) == 0

    def test_claim_biz_error_is_attention(self, monkeypatch, no_cookies):
        monkeypatch.setattr("auto_signin_kit.domains.doubao.api.call", lambda *a, **k: (
            200, {"code": 710010702, "msg": "系统错误", "data": None}, None))
        assert service.run_action("claim", _config(no_cookies)) == 1

    def test_claim_network_error_is_attention(self, monkeypatch, no_cookies):
        monkeypatch.setattr("auto_signin_kit.domains.doubao.api.call",
                            lambda *a, **k: (0, None, "connection refused"))
        assert service.run_action("claim", _config(no_cookies)) == 1

    def test_dry_run_does_not_checkin(self, monkeypatch, no_cookies):
        seen = []

        def fake_call(url, **kwargs):
            seen.append(url)
            return 200, {"code": 0, "data": {}}, None

        monkeypatch.setattr("auto_signin_kit.domains.doubao.api.call", fake_call)
        assert service.run_action("claim", _config(no_cookies), dry_run=True) == 0
        assert "/alice/ug/campaign/landing/get" in seen[0]  # 只查落地页,不 POST checkin

    def test_status_campaign_gone_is_inactive(self, monkeypatch, no_cookies):
        monkeypatch.setattr("auto_signin_kit.domains.doubao.api.call", lambda *a, **k: (
            200, {"code": 2, "msg": "campaign not found", "data": None}, None))
        assert service.run_action("status", _config(no_cookies)) == 1

    def test_unknown_action_is_exit_2(self, no_cookies):
        assert service.run_action("bogus", _config(no_cookies)) == 2

    def test_cookie_error_maps_to_no_auth(self, monkeypatch, tmp_path):
        def raise_no_auth(user_data_dir=None):
            raise cookies_mod.DoubaoCookieError("登录态不完整: 缺 sessionid")

        monkeypatch.setattr(cookies_mod, "load_cookie_header", raise_no_auth)
        assert service.run_action("claim", _config(tmp_path)) == 2  # DoubaoCookieError.exit_code


class TestCli:
    def test_help_lists_commands(self):
        result = CliRunner().invoke(app, ["--help"])
        assert result.exit_code == 0
        for cmd in ("auto", "status", "doctor"):
            assert cmd in result.output

    def test_doctor_reports_offline(self, monkeypatch):
        monkeypatch.setattr(cookies_mod, "doctor_report",
                            lambda user_data_dir=None: {"session_ok": False,
                                                        "session_error": "Cookie 库不存在"})
        result = CliRunner().invoke(app, ["doctor"])
        assert result.exit_code == 1
