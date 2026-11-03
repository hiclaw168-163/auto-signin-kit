"""新域脚手架服务层: 校验 → 生成 → entry-points 注册 → 失败回滚。

纯业务逻辑,不 import typer(交互由 cli.py 与 ui/api 注入);文件写入
仅在 editable 安装(project_root 可定位)时可用——脚手架是开发流程
自动化,不是运行时动态性(SPEC-11)。
"""

from __future__ import annotations

import json
import keyword
import re
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import entry_points
from pathlib import Path

from auto_signin_kit.core.adapters.log_dir import project_root
from auto_signin_kit.core.errors import ConfigError
from auto_signin_kit.scaffolding.templates import Vars, render

#: 生成清单文件名(数据目录下,重建进程失败回滚的依据)。
MANIFEST_NAME = "scaffold-manifest.json"

_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

_ENTRY_SECTIONS = (
    ('[project.entry-points."auto_signin_kit.domains"]',
     lambda n: '%s = "auto_signin_kit.domains.%s:app"' % (n, n)),
    ('[project.entry-points."auto_signin_kit.schedule_meta"]',
     lambda n: '%s = "auto_signin_kit.domains.%s.schedule_meta"' % (n, n)),
)


def _known_domains() -> set[str]:
    return {ep.name for ep in entry_points(group="auto_signin_kit.domains")}


def validate(name: str, kind: str, base_url: str, daily_time: str) -> None:
    """非法输入显式报错(不静默兜底);全部合法则静默返回。"""
    if not _DOMAIN_RE.match(name or ""):
        raise ConfigError(
            "域名 %r 非法: 须为小写字母开头的小写字母/数字/下划线标识符" % name)
    if keyword.iskeyword(name):
        raise ConfigError("域名 %r 是 Python 关键字,不能用" % name)
    if name in _known_domains():
        raise ConfigError("域名 %r 已存在(entry_points 已注册同名域)" % name)
    if kind not in ("web", "client"):
        raise ConfigError("kind 只能是 web 或 client,得到 %r" % kind)
    if not base_url.startswith(("http://", "https://")):
        raise ConfigError("base_url 必须以 http(s):// 开头,得到 %r" % base_url)
    if not _TIME_RE.match(daily_time or ""):
        raise ConfigError("daily_time 必须是 HH:MM 24 小时制,得到 %r" % daily_time)
    if project_root() is None:
        raise ConfigError("脚手架需要 editable 安装(无法定位项目根)")


@dataclass(frozen=True)
class Manifest:
    """生成清单: 回滚依据(文件清单 + pyproject 原文)。"""

    domain: str
    files: list[str]
    pyproject_backup: str
    created_at: str

    def dump(self, path: Path) -> None:
        """原子落盘清单(重建进程失败回滚的依据)。"""
        path.write_text(json.dumps({
            "domain": self.domain, "files": self.files,
            "pyproject_backup": self.pyproject_backup,
            "created_at": self.created_at,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def load(path: Path) -> Manifest | None:
        """读清单;文件不存在/损坏/字段缺失一律 None(按无清单处理)。"""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return Manifest(raw["domain"], raw["files"], raw["pyproject_backup"],
                            raw["created_at"])
        except (OSError, ValueError, KeyError, TypeError):
            return None


def _register_entry_points(pyproject: Path, name: str) -> str:
    """在 pyproject 两个 entry-points section 插入注册行;返回原文供回滚。"""
    text = pyproject.read_text(encoding="utf-8")
    backup = text
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    for line in lines:
        out.append(line)
        stripped = line.strip()
        for header, make in _ENTRY_SECTIONS:
            if stripped == header:
                out.append(make(name) + "\n")
                break
    pyproject.write_text("".join(out), encoding="utf-8")
    return backup


def _unregister_entry_points(pyproject: Path, backup: str) -> None:
    pyproject.write_text(backup, encoding="utf-8")


def create_domain(name: str, kind: str, display: str | None, base_url: str,
                  daily_time: str) -> Manifest:
    """生成域骨架并注册;返回清单(重建进程据此在失败时回滚)。

    步骤内任一失败立即回滚已写文件,不留半成品域。
    """
    validate(name, kind, base_url, daily_time)
    root = project_root()
    if root is None:  # validate 已挡,这里只为类型收窄
        raise ConfigError("无法定位项目根")
    v = Vars(name=name, kind=kind, display=display or name,
             base_url=base_url.rstrip("/"), daily_time=daily_time)
    files = render(v)
    written: list[Path] = []
    try:
        for rel, content in files.items():
            target = root / rel
            if target.exists():
                raise ConfigError("目标文件已存在,拒绝覆盖: %s" % rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(target)
        pyproject = root / "pyproject.toml"
        backup = _register_entry_points(pyproject, name)
    except BaseException:
        for target in written:
            target.unlink(missing_ok=True)
            if target.parent != root and not any(target.parent.iterdir()):
                target.parent.rmdir()
        raise
    manifest = Manifest(domain=name, files=[str(p.relative_to(root)) for p in written],
                        pyproject_backup=backup,
                        created_at=datetime.now().astimezone().isoformat(timespec="seconds"))
    manifest.dump(_manifest_path())
    return manifest


def rollback(manifest: Manifest) -> None:
    """按清单回滚: 删除生成文件 + 恢复 pyproject。幂等,失败不抛。"""
    root = project_root()
    if root is None:
        return
    for rel in manifest.files:
        target = root / rel
        target.unlink(missing_ok=True)
        parent = target.parent
        if parent != root and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    _unregister_entry_points(root / "pyproject.toml", manifest.pyproject_backup)
    _manifest_path().unlink(missing_ok=True)


def _manifest_path() -> Path:
    from auto_signin_kit.core.adapters.log_dir import default_data_dir

    return default_data_dir() / MANIFEST_NAME
