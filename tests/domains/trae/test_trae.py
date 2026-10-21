"""trae 域测试: SS_AES 解密回环 / 业务映射 / CLI 冒烟(本机凭据隔离)。"""

import base64
import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auto_signin_kit.core.adapters.win_dpapi import encrypt_cbc
from auto_signin_kit.domains.trae import auth as auth_m
from auto_signin_kit.domains.trae import service
from auto_signin_kit.domains.trae.auth import SS_AES_HEADER, AuthFormatError, NoAuthError
from auto_signin_kit.domains.trae.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """测试永远看不到本机真实 Trae 登录态;令牌来源全部换掉。"""
    monkeypatch.delenv("TRAE_TOKEN", raising=False)
    monkeypatch.delenv("TRAE_AUTH_DIR", raising=False)
    monkeypatch.setenv("TRAE_AUTH_DIR", str(tmp_path / "empty-trae"))
    return tmp_path


def _make_envelope(payload: dict) -> str:
    """按 auth 模块公开的协议构造一封可解的 SS_AES 信封(加密侧镜像)。"""
    master = bytes(range(32))
    salt = bytes(auth_m._GLE[i] ^ auth_m._ZLE[i] for i in range(64))
    digest = hashlib.sha512(hashlib.sha512(master).digest() + salt).digest() + b"\x00" * 64
    body = json.dumps(payload).encode("utf-8")
    plain = hashlib.sha512(body).digest() + body
    cipher = encrypt_cbc(digest[:16], digest[16:32], plain)
    raw = SS_AES_HEADER + master + cipher
    return base64.b64encode(raw).decode()


def _fake_storage(tmp_path: Path, payload: dict | None, *, provider="icube.cloudide") -> Path:
    root = tmp_path / "trae-root"
    storage = root / "User" / "globalStorage" / "storage.json"
    storage.parent.mkdir(parents=True)
    content = {
        ("iCubeAuthInfo://" + provider): _make_envelope(payload)
    } if payload else {}
    storage.write_text(json.dumps(content), encoding="utf-8")
    return root


INFO = {"token": "tok-1234567890abcdefg", "expiredAt": "2099-01-01T00:00:00Z",
        "userId": "1", "account": {"username": "u"}}


class TestSSAESDecrypt:
    def test_roundtrip_reads_token_and_expiry(self, tmp_path):
        root = _fake_storage(tmp_path, INFO)
        info = auth_m.load_session(str(root))
        assert info["token"] == "tok-1234567890abcdefg"
        assert info["expiredAt"] == "2099-01-01T00:00:00Z"
        assert info["account"]["username"] == "u"

    def test_fallback_to_any_provider_key(self, tmp_path):
        root = _fake_storage(tmp_path, INFO, provider="icube.some-other")
        info = auth_m.load_session(str(root))
        assert info["token"].startswith("tok-")

    def test_corrupted_checksum_reports_format_error(self, tmp_path):
        root = _fake_storage(tmp_path, INFO)
        storage = root / "User" / "globalStorage" / "storage.json"
        data = json.loads(storage.read_text(encoding="utf-8"))
        raw = bytearray(base64.b64decode(next(iter(data.values()))))
        raw[-1] ^= 0xFF  # 破坏校验和
        data[next(iter(data))] = base64.b64encode(bytes(raw)).decode()
        storage.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(AuthFormatError):
            auth_m.load_session(str(root))

    def test_bad_header_rejected(self, tmp_path):
        root = _fake_storage(tmp_path, INFO)
        bad = json.loads((root / "User" / "globalStorage" / "storage.json").read_text("utf-8"))
        key, enc = next(iter(bad.items()))
        bad[key] = base64.b64encode(
            b"\x00\x01\x02\x03\x04\x05" + base64.b64decode(enc)[6:]).decode()
        (root / "User" / "globalStorage" / "storage.json").write_text(
            json.dumps(bad), encoding="utf-8")
        with pytest.raises(AuthFormatError):
            auth_m.load_session(str(root))

    def test_missing_storage_is_no_auth(self, tmp_path):
        with pytest.raises(NoAuthError):
            auth_m.load_session(str(tmp_path / "no-trase-here"))


class _FakeApi:
    """duck-typed Api: status/claim 直接喂响应元组。"""

    def __init__(self, status=(200, {"code": 0}, None), claim=(200, {"code": 0}, None)):
        self.status_resp, self.claim_resp = status, claim
        self.token_expires_at = None

    def status(self, *, budget, retry=True):
        budget.remaining()
        return self.status_resp

    def claim(self, *, budget):
        budget.remaining()
        return self.claim_resp


class _NoBudget:
    def remaining(self):
        return 60.0


def _status_resp(checked: bool, credits=100) -> dict:
    return {"code": 0, "checked_in": checked, "credits": credits,
            "extra_credits": 0 if not checked else credits, "enable": True}


class TestServiceMapping:
    def test_claim_when_not_checked_claims(self):
        api = _FakeApi(
            status=(200, _status_resp(False), None),
            claim=(200, _status_resp(True), None))
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "CLAIMED"
        assert out["credits"] == 100

    def test_claim_when_checked_is_already(self):
        api = _FakeApi(status=(200, _status_resp(True), None))
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "ALREADY"
        assert out["credits"] == 100

    def test_claim_dry_run_reports_without_claiming(self):
        api = _FakeApi(status=(200, _status_resp(False), None))
        out = service.claim_once(api, _NoBudget(), dry_run=True)
        assert out["result"] == "DRY_RUN"

    def test_business_1001_maps_to_auth_rejected(self):
        api = _FakeApi(status=(200, {"code": 1001, "enable": False, "message": "no auth"}, None))
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "AUTH_REJECTED"

    def test_network_error_maps(self):
        api = _FakeApi(status=(0, None, "连接超时"))
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "NETWORK"

    def test_http_401_maps_to_auth_rejected(self):
        api = _FakeApi(status=(401, {}, None))
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "AUTH_REJECTED"


class TestCliSmoke:
    def test_status_no_auth_exits_2(self):
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 2
        payload = json.loads(result.stdout)
        assert payload["result"] == "NO_AUTH"
        assert payload["looked_in"]

    def test_doctor_no_auth_exits_2(self):
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 2
        payload = json.loads(result.stdout)
        assert payload["result"] == "NO_AUTH"

    def test_auto_no_auth_exits_2(self):
        result = runner.invoke(app, ["auto"])
        assert result.exit_code == 2
        payload = json.loads(result.stdout)
        assert payload["result"] == "NO_AUTH"

    def test_claim_missing_token_file_reports_no_auth(self, tmp_path):
        """配置 data_dir 优先于目录探测(直接透传 auth.load_session 覆盖参数)。"""
        root = _fake_storage(tmp_path, INFO)
        cfg = type("Cfg", (), {"data_dir": str(root), "base_url": "https://x",
                               "budget_seconds": 240.0, "log_empty": False})()
        from auto_signin_kit.domains.trae.api import build_api
        api, info, staleness = build_api(cfg)
        assert staleness is None
        assert info["token"] == INFO["token"]
