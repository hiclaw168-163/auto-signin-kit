"""scaffolding 服务层测试: 校验 / 生成 / 注册 / 回滚(全离线,tmp 隔离)。

project_root 与数据目录都 monkeypatch 到 tmp;pyproject 用含两个
entry-points section 的最小样本。
"""

from __future__ import annotations

import pytest

from auto_signin_kit.core.errors import ConfigError
from auto_signin_kit.scaffolding import service as svc

PYPROJECT = '''[project]
name = "x"

[project.entry-points."auto_signin_kit.domains"]
trae = "auto_signin_kit.domains.trae:app"

[project.entry-points."auto_signin_kit.schedule_meta"]
trae = "auto_signin_kit.domains.trae.schedule_meta"
'''


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(svc, "project_root", lambda: tmp_path)
    monkeypatch.setattr(svc, "_manifest_path",
                        lambda: tmp_path / "data" / "scaffold-manifest.json")
    monkeypatch.setattr(svc, "_known_domains", lambda: {"trae"})
    return tmp_path


def test_validate_rejects_bad_inputs():
    for kwargs in (
        {"name": "Bad-Name", "kind": "web", "base_url": "https://x", "daily_time": "09:05"},
        {"name": "class", "kind": "web", "base_url": "https://x", "daily_time": "09:05"},
        {"name": "trae", "kind": "web", "base_url": "https://x", "daily_time": "09:05"},
        {"name": "ok_name", "kind": "plugin", "base_url": "https://x", "daily_time": "09:05"},
        {"name": "ok_name", "kind": "web", "base_url": "x.com", "daily_time": "09:05"},
        {"name": "ok_name", "kind": "web", "base_url": "https://x", "daily_time": "25:00"},
    ):
        with pytest.raises(ConfigError):
            svc.validate(**kwargs)


def test_validate_accepts_good_input():
    svc.validate("ok_name", "client", "https://x.com", "09:05")


def test_create_domain_writes_files_and_registers(fake_root):
    manifest = svc.create_domain("demo_site", "web", "Demo Site",
                                 "https://demo.example.com", "08:30")
    assert (fake_root / "src/auto_signin_kit/domains/demo_site/cli.py").is_file()
    assert (fake_root / "config/domains/demo_site.yaml").is_file()
    assert (fake_root / "tests/domains/demo_site/test_contract.py").is_file()
    text = (fake_root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'demo_site = "auto_signin_kit.domains.demo_site:app"' in text
    assert 'demo_site = "auto_signin_kit.domains.demo_site.schedule_meta"' in text
    assert len(manifest.files) == 9


def test_create_domain_refuses_existing_files(fake_root):
    blocked = fake_root / "config/domains/demo_site.yaml"
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.write_text("x", encoding="utf-8")
    with pytest.raises(ConfigError, match="拒绝覆盖"):
        svc.create_domain("demo_site", "web", None, "https://x.com", "08:30")
    # 半成品已回滚: pyproject 未动、已写文件已删
    assert 'demo_site' not in (fake_root / "pyproject.toml").read_text(encoding="utf-8")
    assert not (fake_root / "src/auto_signin_kit/domains/demo_site").exists()


def test_rollback_restores_everything(fake_root):
    svc.create_domain("demo_site", "web", None, "https://x.com", "08:30")
    manifest = svc.Manifest.load(fake_root / "data/scaffold-manifest.json")
    assert manifest is not None
    svc.rollback(manifest)
    assert not (fake_root / "src/auto_signin_kit/domains/demo_site").exists()
    assert (fake_root / "pyproject.toml").read_text(encoding="utf-8") == PYPROJECT


def test_generated_contract_tests_importable(fake_root, monkeypatch):
    """生成物的 Python 语法可编译(不实际 import,避免注册副作用)。"""
    import py_compile

    svc.create_domain("demo_site", "client", None, "https://x.com", "08:30")
    d = fake_root / "src/auto_signin_kit/domains/demo_site"
    for py in d.glob("*.py"):
        py_compile.compile(str(py), doraise=True)
    py_compile.compile(str(fake_root / "tests/domains/demo_site/test_contract.py"),
                       doraise=True)
