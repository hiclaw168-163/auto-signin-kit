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
    """duck-typed Api: status/claim 直接喂响应元组;status 可给序列按次翻转。"""

    def __init__(self, status=(200, {"code": 0}, None), claim=(200, {"code": 0}, None)):
        self.status_resps = list(status) if isinstance(status, list) else [status]
        self.claim_resp = claim
        self.token_expires_at = None

    def status(self, *, budget, retry=True):
        budget.remaining()
        if len(self.status_resps) > 1:
            return self.status_resps.pop(0)
        return self.status_resps[0]

    def claim(self, *, budget):
        budget.remaining()
        return self.claim_resp


class _SeqClaimApi:
    """claim 按序返回预设响应,用于模拟限流后恢复。"""

    def __init__(self, claim_responses):
        self.responses = list(claim_responses)
        self.calls = 0
        self.token_expires_at = None

    def status(self, *, budget, retry=True):
        return 200, _status_resp(False), None

    def claim(self, *, budget):
        budget.remaining()
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


class _NoBudget:
    def remaining(self):
        return 60.0


def _status_resp(checked: bool, credits=100) -> dict:
    return {"code": 0, "checked_in": checked, "credits": credits,
            "extra_credits": 0 if not checked else credits, "enable": True}


class TestServiceMapping:
    def test_claim_when_not_checked_claims(self):
        # 真实形状: claim 只回 {"code": 0},由 status 复查确认(2026-10-22)
        api = _FakeApi(
            status=[(200, _status_resp(False), None), (200, _status_resp(True), None)],
            claim=(200, {"code": 0, "message": "success"}, None))
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


class TestClaimConfirm:
    """claim 受理(code=0)后用 status 复查确认;响应回显 checked_in 时走快速路径。"""

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(service.time, "sleep", lambda _: None)

    def test_claim_body_echo_is_trusted_without_status_poll(self):
        api = _FakeApi(
            status=(200, _status_resp(False), None),
            claim=(200, _status_resp(True), None))
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "CLAIMED"
        assert out["credits"] == 100

    def test_status_confirms_after_claim_lag(self):
        # 复查第一轮状态还没翻转,第二轮确认(服务端状态延迟)
        api = _FakeApi(
            status=[(200, _status_resp(False), None),
                    (200, _status_resp(False), None),
                    (200, _status_resp(True), None)],
            claim=(200, {"code": 0, "message": "success"}, None))
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "CLAIMED"

    def test_unconfirmed_claim_reports_error(self):
        api = _FakeApi(
            status=(200, _status_resp(False), None),
            claim=(200, {"code": 0, "message": "success"}, None))
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "ERROR"
        assert out["needs_attention"] is True
        assert "code=0" in out["report"]


class TestRateLimitRetry:
    """9074 限流在同轮内退避重试(claim 幂等);其他业务错误不重试。"""

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(service.time, "sleep", lambda _: None)

    def test_rate_limit_then_success_claims(self):
        api = _SeqClaimApi([
            (200, {"code": 9074, "message": "too many"}, None),
            (200, _status_resp(True), None),
        ])
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "CLAIMED"
        assert api.calls == 2

    def test_persistent_rate_limit_gives_up_after_three_attempts(self):
        api = _SeqClaimApi([(200, {"code": 9074, "message": "too many"}, None)])
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "RATE_LIMITED"
        assert api.calls == 3

    def test_rate_limit_gives_up_when_budget_runs_out(self):
        class _TinyBudget:
            def remaining(self):
                return 5.0  # 撑不起 20s 退避

        api = _SeqClaimApi([(200, {"code": 9074, "message": "too many"}, None)])
        out = service.claim_once(api, _TinyBudget())
        assert out["result"] == "RATE_LIMITED"
        assert api.calls == 1

    def test_other_business_error_is_not_retried(self):
        api = _SeqClaimApi([(200, {"code": 9004, "message": "bad order"}, None)])
        out = service.claim_once(api, _NoBudget())
        assert out["result"] == "ERROR"
        assert out["code"] == 9004
        assert api.calls == 1


class TestDeviceMetaLockRetry:
    """开机补跑与 IDE 启动同时碰 storage.json: 锁冲突要重试,不能直接放弃设备头。"""

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(auth_m.time, "sleep", lambda _: None)
        monkeypatch.setattr(auth_m, "data_roaming_roots", lambda _dir=None: [Path("fake")])

    def test_locked_then_readable_returns_device_meta(self, monkeypatch):
        reads = iter([("storage", {"telemetry.devDeviceId": "dev-1"})])

        def read(_root):
            try:
                return next(reads)
            except StopIteration:
                raise PermissionError("file locked") from None

        monkeypatch.setattr(auth_m, "_read_storage_file", read)
        meta = auth_m.device_meta()
        assert meta == {"aha_device_id": None, "device_id": "dev-1", "app_version": None}

    def test_persistent_lock_degrades_after_three_reads(self, monkeypatch):
        calls = {"n": 0}

        def always_locked(_root):
            calls["n"] += 1
            raise PermissionError("file locked")

        monkeypatch.setattr(auth_m, "_read_storage_file", always_locked)
        assert auth_m.device_meta() == {}
        assert calls["n"] == 3


class TestAhaDeviceId:
    """Aha 数字设备号: 签到 x-device-id 的首选,UUID 形态会触发 9074 风控。"""

    def test_extracted_from_icube_dc_key_name(self):
        storage = {
            "iCubeAuthInfo://icube-dc:1234567890123456": "encrypted-blob",
            "telemetry.devDeviceId": "8f2e7a1c-uuid-form",
        }
        assert auth_m.aha_device_id(storage) == "1234567890123456"

    def test_invalid_candidates_are_rejected(self):
        assert auth_m.aha_device_id({
            "iCubeAuthInfo://icube-dc:not-a-number": "x",          # 非数字
            "iCubeAuthInfo://icube-dc:123": "x",                    # 太短(<8)
            "iCubeAuthInfo://icube-dc:1" + "2" * 25: "x",           # 太长(>20)
        }) is None
        assert auth_m.aha_device_id({}) is None

    def test_device_meta_prefers_aha_id(self, monkeypatch):
        monkeypatch.setattr(auth_m, "data_roaming_roots", lambda _dir=None: [Path("fake")])
        monkeypatch.setattr(auth_m, "_read_storage_file", lambda _root: (
            "storage", {
                "iCubeAuthInfo://icube-dc:9876543210987654": "blob",
                "telemetry.devDeviceId": "uuid-form",
                "iCubeLastVersion": "1.107.1",
            }))
        assert auth_m.device_meta() == {
            "aha_device_id": "9876543210987654",
            "device_id": "uuid-form",
            "app_version": "1.107.1",
        }

    def test_headers_prefer_aha_device_id_and_match_client_fingerprint(self):
        from auto_signin_kit.domains.trae.api import Api

        api = Api("tok", "https://api.trae.cn", device={
            "aha_device_id": "9876543210987654",
            "device_id": "uuid-form",
            "app_version": "1.107.1",
        })
        headers = api._headers()
        assert headers["x-device-id"] == "9876543210987654"  # 数字号优先于 UUID
        assert headers["User-Agent"] == "Trae/1.107.1"
        assert headers["X-User-Region"] == "cn"

    def test_headers_fall_back_to_dev_device_id(self):
        from auto_signin_kit.domains.trae.api import Api

        api = Api("tok", "https://api.trae.cn", device={
            "aha_device_id": None, "device_id": "uuid-form", "app_version": None,
        })
        headers = api._headers()
        assert headers["x-device-id"] == "uuid-form"  # 如实降级,不伪造数字号
        assert "User-Agent" not in headers


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
