"""ui accounts-sync 命令测试(全离线: 假 providers,tmp 台账)。"""

from __future__ import annotations

import types

from typer.testing import CliRunner

from auto_signin_kit.ui import cli as ui_cli


def _patch_providers(monkeypatch, collect) -> None:
    """注册表只出一个声明了 client_info 的假域,采集真实落 tmp 台账。

    声明式应用一并清空——否则本机 config/apps/*.yaml(如 trae)会混进
    同步结果,测试结论就不只取决于注入的假域了。
    """
    meta = types.SimpleNamespace(client_info=collect)
    monkeypatch.setattr("auto_signin_kit.ui.apps_sync.providers",
                        lambda: iter([("qoder", meta)]))
    monkeypatch.setattr("auto_signin_kit.ui.apps_sync._declarative_apps",
                        lambda: [])


def test_accounts_sync_prints_summary(monkeypatch):
    _patch_providers(monkeypatch, lambda: {"version": "0.4.8"})
    out = CliRunner().invoke(ui_cli.app, ["accounts-sync"])
    assert out.exit_code == 0
    assert "已同步 qoder" in out.output
    assert "0.4.8" in out.output
    assert "账户总览: 登记 1 个,近 7 天活跃 0 个" in out.output
    assert "上限未设定" in out.output  # 上限是人工设定的业务输入,不猜


def test_accounts_sync_partial_failure_exits_1(monkeypatch):
    def boom():
        raise RuntimeError("探测失败")

    _patch_providers(monkeypatch, boom)
    out = CliRunner().invoke(ui_cli.app, ["accounts-sync"])
    assert out.exit_code == 1  # 部分失败如实反映在退出码,脚本可判断
    assert "失败  qoder" in out.output
    assert "探测失败" in out.output
    assert "账户总览: 登记 0 个" in out.output  # 没采集到就没建档,不静默
