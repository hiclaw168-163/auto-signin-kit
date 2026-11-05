"""doubao 域测试: cookie 解密链路、API 契约、service 动作映射(全 mock,不碰真实客户端)。"""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.httpclient import Budget
from auto_signin_kit.domains.doubao_cn import cdp as cdp_mod
from auto_signin_kit.domains.doubao_cn import cookies as cookies_mod
from auto_signin_kit.domains.doubao_cn import service
from auto_signin_kit.domains.doubao_cn.api import COMMON_QUERY, DoubaoApi
from auto_signin_kit.domains.doubao_cn.cli import app
from auto_signin_kit.domains.doubao_cn.schema import DoubaoDomainConfig

PREFIX = b"P" * cookies_mod.APP_LAYER_VALUE_PREFIX
FAKE_KEY = b"K" * 32

WIN_COOKIES_REL = Path("Default") / "Network" / "Cookies"
WIN_PROFILES_GLOB = "Profile */Network/Cookies"


@pytest.fixture
def fake_crypto(monkeypatch):
    """固定 Windows 文件解密分支 + 替换 DPAPI/GCM:
    unprotect 返回固定密钥,decrypt_gcm 返回 前缀+明文;CDP 兜底关闭。"""
    monkeypatch.setattr(cookies_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(cookies_mod, "COOKIES_REL", WIN_COOKIES_REL)
    monkeypatch.setattr(cookies_mod, "_PROFILES_GLOB", WIN_PROFILES_GLOB)
    monkeypatch.setattr(cookies_mod, "_copy_locked_file",
                        lambda src, dst: dst.write_bytes(src.read_bytes()))
    monkeypatch.setattr(cookies_mod, 'cookies_via_cdp', lambda port=None: None)
    monkeypatch.setattr(cookies_mod.cdp, "probe", lambda port=None: False)
    monkeypatch.setattr(cookies_mod, "unprotect", lambda data: FAKE_KEY)
    monkeypatch.setattr(cookies_mod, "decrypt_gcm", lambda key, nonce, blob: PREFIX + blob)


def _make_user_data(tmp_path, rows):
    """构造假豆包数据目录: Local State + Cookies 库(明文值在 fake_crypto 下解出)。"""
    root = tmp_path / "User Data"
    root.mkdir(parents=True)
    (root / "Local State").write_text(json.dumps(
        {"os_crypt": {"encrypted_key": base64.b64encode(b"DPAPI" + b"x" * 32).decode()}}),
        encoding="utf-8")
    db = root / WIN_COOKIES_REL
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
        monkeypatch.setattr(cookies_mod, "IS_WINDOWS", True)
        monkeypatch.setattr(cookies_mod, "COOKIES_REL", WIN_COOKIES_REL)
        monkeypatch.setattr(cookies_mod, "_PROFILES_GLOB", WIN_PROFILES_GLOB)
        monkeypatch.setattr(cookies_mod, "_copy_locked_file",
                            lambda src, dst: dst.write_bytes(src.read_bytes()))
        monkeypatch.setattr(cookies_mod, "unprotect", lambda data: FAKE_KEY)
        monkeypatch.setattr(cookies_mod.cdp, "probe", lambda port=None: False)
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

    def test_locked_db_and_no_cdp_report_both_channels(self, tmp_path, fake_crypto,
                                                        monkeypatch):
        """文件库被锁 + CDP 不可达: 两个通道的真实失败原因都要出现在报错里,
        不允许包装成模糊的"登录态不完整"。"""
        monkeypatch.delenv("DOUBAO_CDP_PORT", raising=False)
        root = _make_user_data(tmp_path, [(".doubao.com", "sessionid", "x")])
        monkeypatch.setattr(cookies_mod, "_copy_locked_file",
                            lambda src, dst: (_ for _ in ()).throw(
                                cookies_mod.DoubaoCookieError(
                                    "Cookie 库被运行中的客户端独占锁定(WinError 32)")))
        with pytest.raises(cookies_mod.DoubaoCookieError) as exc:
            cookies_mod.load_cookie_header(root)
        msg = str(exc.value)
        assert "两个读取通道均失败" in msg
        assert "独占锁定" in msg  # 文件通道的真实原因
        assert "9222" in msg  # CDP 通道的真实原因(端口不可达)

    def test_missing_cookie_with_readable_db_is_real_incomplete(self, tmp_path, fake_crypto):
        """文件解密成功但缺字段: 磁盘登录态确实不完整,如实报"登录态不完整"。"""
        root = _make_user_data(tmp_path, [(".doubao.com", "sid_tt", "abc")])
        with pytest.raises(cookies_mod.DoubaoCookieError) as exc:
            cookies_mod.load_cookie_header(root)
        assert "登录态不完整" in str(exc.value)
        assert "已过期" in str(exc.value)

    def test_doctor_reports_locked_db_as_explicit_check(self, tmp_path, fake_crypto,
                                                        monkeypatch):
        monkeypatch.delenv("DOUBAO_CDP_PORT", raising=False)
        root = _make_user_data(tmp_path, [(".doubao.com", "sessionid", "x")])
        monkeypatch.setattr(cookies_mod, "_db_locked", lambda p: True)
        report = cookies_mod.doctor_report(root)
        assert [Path(s).as_posix() for s in report["cookie_db_locked"]] == \
            ["Default/Network/Cookies"]
        assert report["cdp_available"] is False


def _make_mac_user_data(tmp_path):
    """mac 布局: 目录即 Doubao 本身,Default/Cookies(无 Network/ 子层)。"""
    root = tmp_path / "Doubao"
    (root / "Default").mkdir(parents=True)
    (root / "Local State").write_text("{}", encoding="utf-8")
    db = root / "Default" / "Cookies"
    con = sqlite3.connect(str(db))
    con.execute("create table cookies (host_key text, name text, encrypted_value blob)")
    con.execute("insert into cookies values (?,?,?)",
                (".doubao.com", "sessionid", b"v10" + b"N" * 12 + b"stale"))
    con.commit()
    con.close()
    return root


class TestMacPlatformBranch:
    """mac: 文件解密如实报 UNSUPPORTED,链路只走 CDP(见 NOTE-04)。"""

    def test_decrypt_unsupported_mentions_cdp_guidance(self, tmp_path, monkeypatch):
        self._patch_mac(monkeypatch)
        root = _make_mac_user_data(tmp_path)
        with pytest.raises(cookies_mod.DoubaoCookieError) as exc:
            cookies_mod.load_cookie_header(root)
        msg = str(exc.value)
        assert "macOS" in msg and "CDP" in msg
        assert "remote-debugging-port" in msg  # 指引可执行

    @staticmethod
    def _patch_mac(monkeypatch):
        """mac 分支跨平台可跑: 布局常量在 import 时按本机平台定死,
        测试必须连同布局一起 patch(否则 Windows 上跑 mac 用例会找 Windows 路径)。"""
        monkeypatch.setattr(cookies_mod, "IS_WINDOWS", False)
        monkeypatch.setattr(cookies_mod, "COOKIES_REL",
                            Path("Default") / "Cookies")
        monkeypatch.setattr(cookies_mod, "_PROFILES_GLOB", "Profile */Cookies")
        monkeypatch.setattr(cookies_mod, "cookies_via_cdp", lambda port=None: None)
        monkeypatch.setattr(cookies_mod.cdp, "probe", lambda port=None: False)

    def test_cdp_live_cookies_supply_session(self, tmp_path, monkeypatch):
        self._patch_mac(monkeypatch)
        live = {"sessionid": "live-sid", "sid_tt": "live-tt", "uid_tt": "live-uid"}
        monkeypatch.setattr(cookies_mod, "cookies_via_cdp", lambda port=None: live)
        root = _make_mac_user_data(tmp_path)
        header = cookies_mod.load_cookie_header(root)
        assert "sessionid=live-sid" in header
        assert "stale" not in header  # 文件库的旧值不参与

    def test_doctor_reports_unsupported_honestly(self, tmp_path, monkeypatch):
        self._patch_mac(monkeypatch)
        root = _make_mac_user_data(tmp_path)
        report = cookies_mod.doctor_report(root)
        assert report["file_decrypt_supported"] is False
        # mac 布局被正确探测(分隔符按本机平台,比较时归一为 /)
        assert [Path(s).as_posix() for s in report["cookie_dbs"]] == ["Default/Cookies"]
        assert report["session_ok"] is False
        assert "CDP" in report["session_error"]

    def test_default_dir_on_mac_has_no_user_data_layer(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cookies_mod, "IS_WINDOWS", False)
        monkeypatch.delenv("DOUBAO_USER_DATA_DIR", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert cookies_mod.default_user_data_dir() == \
            tmp_path / "Library" / "Application Support" / "Doubao"


class _FakeCdpResp:
    """urlopen 假响应: /json/version 的 payload 可控。"""

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    status = 200

    def read(self):
        return json.dumps(self._payload).encode()


class TestCdpIdentity:
    """9222 可能被其他 Chromium 应用占用(实测 Qoder)。

    身份判定设计(2026-11-03 调整): /json/version 的 Browser 字段在新版豆包
    只报 "Chrome/147..."(不含 doubao 标识),按标识校验会把真豆包拒掉;
    probe 只验端口可达,**身份安全由 cookies_via_cdp 的 target URL 过滤
    保证**——异应用占口时过滤后拿不到任何 doubao cookie,返回 None。
    """

    def _patch_version(self, monkeypatch, browser):
        monkeypatch.setattr(
            cdp_mod.urllib.request, "urlopen",
            lambda url, timeout: _FakeCdpResp({"Browser": browser}))

    def test_probe_only_checks_reachability_regardless_of_browser(self, monkeypatch):
        # 新版豆包 Browser 字段是纯 Chrome 标识,也要被判可达
        self._patch_version(monkeypatch, "Chrome/147.0.7727.149")
        assert cdp_mod.probe(9222) is True

    def test_probe_connection_refused_is_false(self, monkeypatch):
        def refuse(url, timeout):
            raise OSError("connection refused")

        monkeypatch.setattr(cdp_mod.urllib.request, "urlopen", refuse)
        assert cdp_mod.probe(9222) is False

    def test_cookies_via_cdp_returns_none_for_foreign_browser(self, monkeypatch, tmp_path):
        # 异应用占口: /json/version 可达,但 /json/list 无任何 doubao target
        # → target URL 过滤后拿不到 cookie,返回 None(不误用别人会话)
        self._patch_version(monkeypatch, "Chrome/120.0.6099.109")

        class _FakeListResp:
            status = 200

            def read(self):
                return json.dumps([
                    {"type": "page", "url": "https://qoder.example/ide",
                     "webSocketDebuggerUrl": "ws://127.0.0.1:9222/x"},
                ]).encode()

        def fake_urlopen(url, timeout):
            return _FakeCdpResp({"Browser": "Chrome/120"}) if "version" in url \
                else _FakeListResp()

        monkeypatch.setattr(cdp_mod.urllib.request, "urlopen", fake_urlopen)
        assert cdp_mod.cookies_via_cdp(port=9222) is None

    def test_invalid_port_env_is_not_available(self, monkeypatch):
        monkeypatch.setenv("DOUBAO_CDP_PORT", "not-a-number")
        assert cookies_mod.cdp_available() is False

    def test_resolve_port_env_wins_over_config_fallback(self, monkeypatch):
        # 环境变量 > 域配置 fallback > 内置默认,三级回落
        monkeypatch.setenv("DOUBAO_CDP_PORT", "9300")
        assert cdp_mod.resolve_port(9223) == 9300

    def test_resolve_port_falls_back_to_config_then_default(self, monkeypatch):
        monkeypatch.delenv("DOUBAO_CDP_PORT", raising=False)
        assert cdp_mod.resolve_port(9223) == 9223
        assert cdp_mod.resolve_port(None) == 9222


class TestDoubaoApi:
    def test_urls_carry_full_common_query(self, monkeypatch):
        captured = {}

        def fake_call(url, **kwargs):
            captured["url"] = url
            return 200, {"code": 0, "data": {}}, None

        monkeypatch.setattr("auto_signin_kit.domains.doubao_cn.api.call", fake_call)
        DoubaoApi("sessionid=x").checkin("pc_incentive_task_mvp_v3")
        assert "/alice/ug/campaign/landing/checkin/" in captured["url"]
        for part in COMMON_QUERY:  # 公共参数缺一不可(取证: 缺了报 system error)
            assert part in captured["url"]

    def test_checkin_body_uses_scene(self, monkeypatch):
        captured = {}

        def fake_call(url, **kwargs):
            captured["payload"] = kwargs.get("payload")
            return 200, {"code": 0, "data": {}}, None

        monkeypatch.setattr("auto_signin_kit.domains.doubao_cn.api.call", fake_call)
        DoubaoApi("sessionid=x").checkin("pc_incentive_task_mvp_v3")
        assert captured["payload"] == {"scene": "pc_incentive_task_mvp_v3"}

    def test_once_disables_retry(self, monkeypatch):
        captured = {}

        def fake_call(url, **kwargs):
            captured["retry"] = kwargs.get("retry")
            return 200, {"code": 0, "data": {}}, None

        monkeypatch.setattr("auto_signin_kit.domains.doubao_cn.api.call", fake_call)
        DoubaoApi("sessionid=x", retry=False).checkin("s")
        assert captured["retry"] is False

    def test_budget_seconds_are_normalized_to_budget_object(self):
        """回归: service.build 传裸秒数,必须归一化成 Budget(否则
        httpclient.call 调 budget.remaining() 直接 AttributeError)。"""
        assert isinstance(DoubaoApi("sessionid=x", budget=60.0).budget, Budget)
        assert DoubaoApi("sessionid=x", budget=60.0).budget.remaining() > 0
        assert DoubaoApi("sessionid=x").budget.remaining() > 239  # 缺省 240
        shared = Budget(30.0)
        assert DoubaoApi("sessionid=x", budget=shared).budget is shared  # 不重复包


def _config(tmp_path):
    return DoubaoDomainConfig(campaign_code="pc_incentive_task_mvp_v3",
                              user_data_dir=str(tmp_path), budget_seconds=60)


@pytest.fixture
def no_cookies(monkeypatch):
    """service 层测试: cookie 链路直接返回假头,绕开文件系统。"""
    monkeypatch.setattr(cookies_mod, "load_cookie_header",
                        lambda user_data_dir=None, cdp_port=None: "sessionid=fake")


def _router(routes):
    """按 URL 子串路由的假传输层: routes = [(子串, (status, body, err)), ...]"""

    def fake_call(url, **kwargs):
        for sub, resp in routes:
            if sub in url:
                return resp
        return 404, {"code": -1}, None

    return fake_call


CHECKIN_ALREADY = ("/landing/checkin", (200, {"code": 0, "data": {
    "checked_in": True, "idempotent": True, "reward_amount": 400,
    "continuous_days": 1, "checkin_date": "2026-11-02"}}, None))
CHECKIN_FRESH = ("/landing/checkin", (200, {"code": 0, "data": {
    "checked_in": True, "idempotent": False, "reward_amount": 200,
    "continuous_days": 1}}, None))
DETAIL_MIXED = ("/v3/detail", (200, {"code": 0, "data": {"tasks": [
    {"title": "每日签到", "campaign_code": "daily_signin", "task_status": 3},
    {"title": "整理文档", "campaign_code": "t_doc", "task_status": 2,
     "reward_summaries": [{"reward_amount": 100}]},
    {"title": "生成PPT", "campaign_code": "t_ppt", "task_status": 1,
     "reward_summaries": [{"reward_amount": 100}]},
], "milestones": [
    {"title": "累计500", "campaign_code": "points_milestone_500",
     "task_status": 1, "reward_summaries": [{"reward_amount": 200}]},
]}}, None))
CLAIM_OK = ("/reward/claim", (200, {"code": 0, "data": {}}, None))


@pytest.fixture
def emitted(monkeypatch):
    """捕获 emit_result 记录,供断言 result/字段。"""
    records = []
    # emit 收口在基类(K8): patch 基类模块符号,所有域实例生效
    monkeypatch.setattr("auto_signin_kit.core.kit.domain.emit_result",
                        lambda out, **kw: records.append(out))
    return records


class TestServiceActions:
    def test_already_with_claimable_task_sweeps_reward(self, monkeypatch, no_cookies, emitted):
        monkeypatch.setattr("auto_signin_kit.domains.doubao_cn.api.call",
                            _router([CHECKIN_ALREADY, DETAIL_MIXED, CLAIM_OK]))
        assert service.run_action("claim", _config(no_cookies), command="auto") == 0
        assert emitted[0]["result"] == "CLAIMED"  # 任务奖励到账
        assert emitted[0]["task_rewards"] == [
            {"code": "t_doc", "title": "整理文档", "amount": 100}]
        assert emitted[0]["task_pending"] == 2  # 1 个未完成任务 + 1 个未达标里程碑

    def test_already_nothing_claimable_stays_already(self, monkeypatch, no_cookies, emitted):
        detail = ("/v3/detail", (200, {"code": 0, "data": {"tasks": [
            {"title": "每日签到", "campaign_code": "daily_signin", "task_status": 3}],
            "milestones": []}}, None))
        monkeypatch.setattr("auto_signin_kit.domains.doubao_cn.api.call",
                            _router([CHECKIN_ALREADY, detail]))
        assert service.run_action("claim", _config(no_cookies)) == 0
        assert emitted[0]["result"] == "ALREADY"

    def test_claim_biz_error_recorded_not_fatal(self, monkeypatch, no_cookies, emitted):
        claim_bad = ("/reward/claim", (200, {"code": 0, "data": {
            "biz_error": {"code": 3,
                          "message": "领取结果确认中，请稍后重试"}}}, None))
        monkeypatch.setattr("auto_signin_kit.domains.doubao_cn.api.call",
                            _router([CHECKIN_ALREADY, DETAIL_MIXED, claim_bad]))
        assert service.run_action("claim", _config(no_cookies)) == 0
        assert emitted[0]["result"] == "ALREADY"  # 领取失败不误报为 CLAIMED
        assert any("领取失败" in part for part in emitted[0]["report"].split("; "))

    def test_fresh_checkin_is_claimed(self, monkeypatch, no_cookies, emitted):
        monkeypatch.setattr("auto_signin_kit.domains.doubao_cn.api.call",
                            _router([CHECKIN_FRESH, DETAIL_MIXED, CLAIM_OK]))
        assert service.run_action("claim", _config(no_cookies)) == 0
        assert emitted[0]["result"] == "CLAIMED"

    def test_claim_biz_error_is_attention(self, monkeypatch, no_cookies, emitted):
        monkeypatch.setattr("auto_signin_kit.domains.doubao_cn.api.call", _router([
            ("/landing/checkin",
             (200, {"code": 710010702, "msg": "系统错误", "data": None}, None))]))
        assert service.run_action("claim", _config(no_cookies)) == 1
        assert emitted[0]["result"] == "ERROR"

    def test_claim_network_error_is_attention(self, monkeypatch, no_cookies, emitted):
        monkeypatch.setattr("auto_signin_kit.domains.doubao_cn.api.call", _router([
            ("/landing/checkin", (0, None, "connection refused"))]))
        assert service.run_action("claim", _config(no_cookies)) == 1
        assert emitted[0]["result"] == "NETWORK"

    def test_dry_run_does_not_checkin(self, monkeypatch, no_cookies, emitted):
        seen = []

        def fake_call(url, **kwargs):
            seen.append(url)
            return 200, {"code": 0, "data": {}}, None

        monkeypatch.setattr("auto_signin_kit.domains.doubao_cn.api.call", fake_call)
        assert service.run_action("claim", _config(no_cookies), dry_run=True) == 0
        assert any("/landing/get" in u for u in seen)  # 只查落地页
        assert not any("/landing/checkin" in u for u in seen)

    def test_detail_query_failure_noted_not_fatal(self, monkeypatch, no_cookies, emitted):
        monkeypatch.setattr("auto_signin_kit.domains.doubao_cn.api.call", _router([
            CHECKIN_ALREADY,
            ("/v3/detail", (200, {"code": 2, "msg": "campaign not found"}, None))]))
        assert service.run_action("claim", _config(no_cookies)) == 0
        assert emitted[0]["result"] == "ALREADY"  # 清扫失败不掩盖签到结果
        assert "活动详情查询失败" in emitted[0]["report"]

    def test_status_campaign_gone_is_inactive(self, monkeypatch, no_cookies, emitted):
        monkeypatch.setattr("auto_signin_kit.domains.doubao_cn.api.call", _router([
            ("/v3/detail", (200, {"code": 2, "msg": "campaign not found", "data": None}, None))]))
        assert service.run_action("status", _config(no_cookies)) == 1

    def test_unknown_action_is_exit_2(self, no_cookies):
        assert service.run_action("bogus", _config(no_cookies)) == 2

    def test_cookie_error_maps_to_no_auth(self, monkeypatch, tmp_path):
        def raise_no_auth(user_data_dir=None, cdp_port=None):
            raise cookies_mod.DoubaoCookieError("登录态不完整: 缺 sessionid")

        monkeypatch.setattr(cookies_mod, "load_cookie_header", raise_no_auth)
        assert service.run_action("claim", _config(tmp_path)) == 2  # DoubaoCookieError.exit_code

    def test_config_cdp_port_flows_to_cookie_loader(self, monkeypatch):
        seen: dict = {}

        def fake_load(user_data_dir=None, cdp_port=None):
            seen["cdp_port"] = cdp_port
            return "sessionid=fake"

        monkeypatch.setattr(cookies_mod, "load_cookie_header", fake_load)
        config = DoubaoDomainConfig(campaign_code="c", user_data_dir="/nonexistent",
                                    budget_seconds=60, cdp_port=9223)
        service.run_action("claim", config)
        assert seen["cdp_port"] == 9223


class TestScheduleSkip:
    """静默调度时当天已成功签到 → 不执行、不 emit、不落库。"""

    def test_silent_claim_skips_when_done_today(self, monkeypatch, emitted, tmp_path):
        monkeypatch.setattr("auto_signin_kit.core.kit.actions.done_today",
                            lambda domain, account="default": True)
        assert service.run_action("claim", _config(tmp_path),
                                  silent=True, command="auto") == 0
        assert emitted == []  # 没有任何 emit → 也就没有落库

    def test_done_check_not_applied_to_manual_run(self, monkeypatch, no_cookies, emitted):
        monkeypatch.setattr("auto_signin_kit.core.kit.actions.done_today",
                            lambda domain, account="default": True)
        monkeypatch.setattr("auto_signin_kit.domains.doubao_cn.api.call",
                            _router([CHECKIN_ALREADY]))
        # 手动执行(silent=False)不受跳过影响,照常执行
        assert service.run_action("claim", _config(no_cookies), command="auto") == 0
        assert emitted[0]["result"] == "ALREADY"


class TestAccountProfile:
    """SPEC-12 P2: 具名账户独立 User Data/CDP 端口;default 路径不变。"""

    def test_account_user_data_dir_is_used(self, monkeypatch, emitted, tmp_path):
        """账户声明的 user_data_dir 优先于全局配置;目录不存在如实 NO_AUTH。"""
        from auto_signin_kit.core.config.schema import AccountRef

        seen: list = []
        monkeypatch.setattr("auto_signin_kit.core.kit.domain.emit_result",
                            lambda out, **kw: seen.append((out, kw.get("account"))))
        missing = tmp_path / "second-user-data"
        cfg = _config(tmp_path)
        cfg = cfg.model_copy(update={"accounts": [
            AccountRef(name="second", user_data_dir=str(missing))]})
        assert service.run_action("claim", cfg, account="second") == 2
        out, acct = seen[-1]
        assert out["result"] == "NO_AUTH" and acct == "second"
        assert str(missing) in out["report"]





class TestCli:
    def test_help_lists_commands(self):
        result = CliRunner().invoke(app, ["--help"])
        assert result.exit_code == 0
        for cmd in ("auto", "status", "doctor"):
            assert cmd in result.output

    def test_doctor_reports_offline(self, monkeypatch):
        monkeypatch.setattr(cookies_mod, "doctor_report",
                            lambda user_data_dir=None, cdp_port=None: {
                                "session_ok": False,
                                "session_error": "Cookie 库不存在"})
        result = CliRunner().invoke(app, ["doctor"])
        assert result.exit_code == 1
