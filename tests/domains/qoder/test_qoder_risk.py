"""qoder 风控头单元测试: 设备组件发现 / 头组装 / 30 分钟缓存(不碰真组件)。"""

import json
import time

import pytest

import auto_signin_kit.core.adapters.native_helper as native_helper
from auto_signin_kit.core.adapters.platform_info import IS_WINDOWS
from auto_signin_kit.domains.qoder import risk as risk_mod
from auto_signin_kit.domains.qoder.risk import find_device_component, risk_headers


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """重置模块级缓存与环境变量: 用例互不污染,也不碰本机真实组件与目录。"""
    monkeypatch.delenv("QODER_UMID_EXE", raising=False)
    monkeypatch.delenv("QODER_AUTH_DIR", raising=False)
    monkeypatch.setattr(risk_mod, "_cache", {"at": 0.0, "root": None, "headers": {}})
    return tmp_path


def _fake_component(tmp_path, monkeypatch, identity):
    """造一个存在的 umid 组件 + build-manifest(与源码同 exe.parent.parent),桩掉子进程。"""
    umid_dir = tmp_path / "app" / "resources" / "umid"
    umid_dir.mkdir(parents=True)
    exe = umid_dir / "runtime-info.exe"
    exe.write_bytes(b"")
    (exe.parent.parent / "build-manifest.json").write_text(
        json.dumps({"productVersion": "9.9.9"}), encoding="utf-8")
    monkeypatch.setenv("QODER_UMID_EXE", str(exe))

    def fake_run_capture(exe_path, args=(), *, timeout=15.0, input_bytes=b"", cwd=None):
        fake_run_capture.count += 1
        return 0, json.dumps(identity).encode("utf-8"), b""

    fake_run_capture.count = 0
    monkeypatch.setattr(native_helper, "run_capture", fake_run_capture)
    return fake_run_capture


class TestFindDeviceComponent:
    def test_override_to_existing_file_hits(self, tmp_path, monkeypatch):
        """QODER_UMID_EXE 指向存在的文件时原样命中。"""
        exe = tmp_path / "runtime-info.exe"
        exe.write_bytes(b"")
        monkeypatch.setenv("QODER_UMID_EXE", str(exe))
        assert find_device_component() == str(exe)

    def test_missing_component_returns_none(self, tmp_path, monkeypatch):
        """候选都不存在时给 None,不编造路径。"""
        monkeypatch.setenv("QODER_UMID_EXE", str(tmp_path / "no-such-exe"))
        assert find_device_component() is None


class TestRunUmid:
    def test_last_json_object_line_wins(self, tmp_path, monkeypatch):
        """stdout 倒序找最后一条能解析的 JSON 行,非 JSON 垃圾行被跳过。"""

        def fake(exe, args=(), *, timeout=15.0, input_bytes=b"", cwd=None):
            return 0, b'noise\nnot json\n{"machineToken": "t1"}\n', b""

        monkeypatch.setattr(native_helper, "run_capture", fake)
        assert risk_mod._run_umid(tmp_path / "runtime-info.exe") == {"machineToken": "t1"}

    def test_nonzero_exit_returns_empty(self, tmp_path, monkeypatch):
        """非零退出码直接空 dict,不解析输出(失败降级为普通请求)。"""

        def fake(exe, args=(), *, timeout=15.0, input_bytes=b"", cwd=None):
            return 1, b'{"machineToken": "t1"}\n', b""

        monkeypatch.setattr(native_helper, "run_capture", fake)
        assert risk_mod._run_umid(tmp_path / "runtime-info.exe") == {}

    def test_non_dict_json_yields_empty(self, tmp_path, monkeypatch):
        """倒序找到的第一条合法 JSON 不是对象时按空处理,不继续往前找。"""

        def fake(exe, args=(), *, timeout=15.0, input_bytes=b"", cwd=None):
            return 0, b'{"k": "v"}\n[1, 2]\n', b""

        monkeypatch.setattr(native_helper, "run_capture", fake)
        assert risk_mod._run_umid(tmp_path / "runtime-info.exe") == {}


class TestRiskHeaders:
    @pytest.mark.skipif(IS_WINDOWS, reason="Windows 会额外带 MachineOS/Hostname 头")
    def test_builds_headers_from_manifest_and_umid(self, tmp_path, monkeypatch):
        """manifest 版本 + 组件输出三件套 → 对应 Cosy 头,缺字段不填空串。"""
        _fake_component(tmp_path, monkeypatch, {
            "machineToken": "tok", "machineCode": "code", "machineType": "pc"})
        assert risk_headers() == {
            "Cosy-Version": "9.9.9",
            "Cosy-MachineToken": "tok",
            "Cosy-MachineCode": "code",
            "Cosy-MachineType": "pc",
        }

    @pytest.mark.skipif(IS_WINDOWS, reason="Windows 会额外带 MachineOS/Hostname 头")
    def test_partial_identity_only_maps_present_fields(self, tmp_path, monkeypatch):
        """组件只回部分字段时只映射存在的头,不出现空值头。"""
        _fake_component(tmp_path, monkeypatch, {"machineToken": "tok"})
        assert risk_headers() == {"Cosy-Version": "9.9.9", "Cosy-MachineToken": "tok"}

    @pytest.mark.skipif(IS_WINDOWS, reason="Windows 会额外带 MachineOS/Hostname 头")
    def test_machine_id_from_auth_dir(self, tmp_path, monkeypatch):
        """auth.machine-id 读取自指定数据目录并去首尾空白。"""
        auth_dir = tmp_path / "auth-dir"
        auth_dir.mkdir()
        (auth_dir / "auth.machine-id").write_text("mid-123\n", encoding="utf-8")
        assert risk_headers(str(auth_dir)) == {"Cosy-MachineId": "mid-123"}

    @pytest.mark.skipif(IS_WINDOWS, reason="Windows 会额外带 MachineOS/Hostname 头")
    def test_cache_hit_within_ttl_returns_copy(self, tmp_path, monkeypatch):
        """30 分钟内命中缓存且不再起子进程;返回副本,改返回值不污染缓存。"""
        fake = _fake_component(tmp_path, monkeypatch, {"machineToken": "tok"})
        first = risk_headers()
        first["Cosy-MachineToken"] = "tampered"
        assert risk_headers() == {"Cosy-Version": "9.9.9", "Cosy-MachineToken": "tok"}
        assert fake.count == 1

    def test_cache_expires_after_ttl(self, tmp_path, monkeypatch):
        """超过 30 分钟缓存过期,重新执行组件调用。"""
        fake = _fake_component(tmp_path, monkeypatch, {"machineToken": "tok"})
        risk_headers()
        risk_mod._cache["at"] = time.time() - 1799  # 差 1 秒到期,仍在窗口内
        risk_headers()
        assert fake.count == 1
        risk_mod._cache["at"] = time.time() - 1801  # 已过期,重新计算
        risk_headers()
        assert fake.count == 2

    def test_cache_miss_when_auth_dir_changes(self, tmp_path, monkeypatch):
        """换 auth_dir 视为缓存未命中,重新取设备标识。"""
        fake = _fake_component(tmp_path, monkeypatch, {"machineToken": "tok"})
        risk_headers(None)
        risk_headers(str(tmp_path / "other-dir"))
        assert fake.count == 2
