"""ui.build 构建编排测试: npm/文件系统全桩,绝不真跑 npm。"""

from __future__ import annotations

import pytest

from auto_signin_kit.core.errors import ConfigError
from auto_signin_kit.ui import build as build_mod


@pytest.fixture
def fake_frontend(monkeypatch, tmp_path):
    """FRONTEND_DIR 指到 tmp;返回 (目录, dist 目录)。"""
    root = tmp_path / "frontend"
    root.mkdir()
    monkeypatch.setattr(build_mod, "FRONTEND_DIR", root)
    return root, root / "dist"


def _fake_npm(monkeypatch: pytest.MonkeyPatch, calls: list[str],
              *, fail_on: str | None = None,
              produce_artifact: bool = True) -> None:
    """把 shutil.which + subprocess.run 换成桩;npm 命令序列记进 calls。"""

    def fake_run(argv: list[str], **kwargs: object):
        cmd = " ".join(argv[1:])
        calls.append(cmd)
        if fail_on and fail_on in argv:
            from types import SimpleNamespace
            return SimpleNamespace(returncode=1, stdout="", stderr="npm error!")
        if produce_artifact and cmd == "run build":
            build_mod.dist_dir().mkdir(parents=True, exist_ok=True)
            (build_mod.dist_dir() / "index.html").write_text("x", encoding="utf-8")
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(build_mod.shutil, "which",
                        lambda name: "npm" if name == "npm" else None)
    monkeypatch.setattr(build_mod.subprocess, "run", fake_run)


def test_is_built_requires_index_html(fake_frontend):
    _, dist = fake_frontend
    dist.mkdir()
    assert build_mod.is_built() is False  # 空 dist 不算构建完成
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    assert build_mod.is_built() is True


def test_ensure_built_skips_when_ready(fake_frontend):
    _, dist = fake_frontend
    dist.mkdir()
    (dist / "index.html").write_text("x", encoding="utf-8")
    assert build_mod.ensure_built(if_missing=True) is False


def test_ensure_built_errors_when_missing_without_if_missing(fake_frontend):
    with pytest.raises(ConfigError, match="尚未构建"):
        build_mod.ensure_built(if_missing=False)


def test_ensure_built_builds_when_missing_with_if_missing(fake_frontend, monkeypatch):
    calls: list[str] = []
    _fake_npm(monkeypatch, calls)
    assert build_mod.ensure_built(if_missing=True) is True
    assert calls == ["install", "run build"]


def test_build_invokes_npm_in_order_and_checks_artifact(fake_frontend, monkeypatch):
    calls: list[str] = []
    _fake_npm(monkeypatch, calls)
    assert build_mod.build() == build_mod.dist_dir()
    assert calls == ["install", "run build"]


def test_build_missing_npm_raises(fake_frontend, monkeypatch):
    monkeypatch.setattr(build_mod.shutil, "which", lambda name: None)
    with pytest.raises(ConfigError, match="Node.js"):
        build_mod.build()


def test_build_npm_failure_surfaces_output(fake_frontend, monkeypatch):
    calls: list[str] = []
    _fake_npm(monkeypatch, calls, fail_on="install")
    with pytest.raises(ConfigError, match="npm error!"):
        build_mod.build()


def test_build_zero_exit_without_artifact_raises(fake_frontend, monkeypatch):
    calls: list[str] = []
    _fake_npm(monkeypatch, calls, produce_artifact=False)
    with pytest.raises(ConfigError, match="dist/index.html 不存在"):
        build_mod.build()
