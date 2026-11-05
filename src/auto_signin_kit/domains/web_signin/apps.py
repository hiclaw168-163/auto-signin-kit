"""声明式应用的加载器: 扫描 ``config/apps/*.yaml`` → AppDeclaration。

约定: 一个应用一份 YAML,文件名(去扩展名)= ``app_code``——文件名与
YAML 内的 ``app_code`` 不一致直接报错,避免"台账里是 A 名、文件叫 B 名"
的错位。坏 YAML 不静默跳过(:func:`all_apps` 严格模式抛错),
:func:`all_apps_safe` 供调度注册等 import 期场景收集错误转 stderr 警告。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from auto_signin_kit.core.config import resolve_config_dir
from auto_signin_kit.core.errors import ConfigError, ConfigMissingError
from auto_signin_kit.domains.web_signin.schema import AppDeclaration

#: 声明文件所在子目录(resolve_config_dir() 之下)。
APPS_SUBDIR = "apps"


def apps_dir() -> Path:
    """声明目录;惰性解析(测试可重定向 AUTO_SIGNIN_KIT_CONFIG_DIR)。"""
    return resolve_config_dir() / APPS_SUBDIR


def _validate(path: Path, data: Any) -> AppDeclaration:
    if not isinstance(data, dict):
        raise ConfigError(f"应用声明格式错误(应为映射): {path}")
    try:
        decl = AppDeclaration.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in e.errors())
        raise ConfigError(f"应用声明校验失败: {path} — {problems}") from e
    if decl.app_code != path.stem:
        raise ConfigError(
            f"应用声明文件名与 app_code 不一致: {path.name} 声明的是 {decl.app_code!r};"
            f"文件名必须与 app_code 相同(重命名文件为 {decl.app_code}.yaml)")
    return decl


def _parse(path: Path) -> Any:
    """读 + 解析 YAML;语法错误统一转 ConfigError(带文件名)。"""
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"应用声明格式错误: {path} — {e}") from e


def load_app(app_code: str) -> AppDeclaration:
    """按编码加载一份声明;不存在时报出当前全部可用编码。"""
    path = apps_dir() / f"{app_code}.yaml"
    if not path.exists():
        available = ", ".join(d.stem for d in sorted(apps_dir().glob("*.yaml"))) or "(无)"
        raise ConfigMissingError(
            f"应用声明不存在: {path}(可用: {available})"
            f";声明式应用请写 config/apps/{app_code}.yaml,规范见 docs/standards/STD-07")
    return _validate(path, _parse(path))


def raw_declaration(app_code: str) -> dict:
    """声明原文(无校验视图): UI 应用配置编辑的取数与回写用。"""
    path = apps_dir() / f"{app_code}.yaml"
    if not path.exists():
        raise ConfigMissingError(f"应用声明不存在: {path}")
    data = _parse(path)
    return data if isinstance(data, dict) else {}


def save_declaration(app_code: str, data: dict) -> Path:
    """校验后原子回写声明(临时文件 + replace);app_code 变更会被校验拦截。"""
    path = apps_dir() / f"{app_code}.yaml"
    decl = _validate(path, data)
    if decl.app_code != app_code:  # 双保险: 文件名与改后的 app_code 仍须一致
        raise ConfigError(
            f"不允许修改 app_code: {app_code} → {decl.app_code};"
            "改名请重命名文件并同步台账")
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(path)
    return path


def all_apps() -> list[AppDeclaration]:
    """加载全部声明;任何一份坏 YAML 都当场炸(不静默少应用)。"""
    return [_validate(path, _parse(path)) for path in sorted(apps_dir().glob("*.yaml"))]


def all_apps_safe() -> tuple[list[AppDeclaration], list[str]]:
    """宽松加载: 返回(可用声明, 坏文件描述列表)。

    仅供 import 期场景(schedule_meta 注册)使用——那里炸掉会让所有 CLI
    命令集体不可用;坏文件以 stderr 警告呈现,真实运行时(load_app)仍会
    严格报错。日常路径请用 :func:`all_apps`。
    """
    import sys

    out: list[AppDeclaration] = []
    broken: list[str] = []
    try:
        paths = sorted(apps_dir().glob("*.yaml"))
    except ConfigError:  # config 目录本身不存在: import 期不炸,真实运行时严格报错
        return [], []
    for path in paths:
        try:
            out.append(_validate(path, yaml.safe_load(path.read_text(encoding="utf-8"))))
        except Exception as exc:  # 单文件坏不影响其他应用注册(严格模式在 all_apps)
            broken.append(f"{path.name}: {exc}")
    if broken:
        sys.stderr.write("警告: 以下应用声明无效,已跳过注册"
                         "(运行 `web_signin list` 看详情):\n  %s\n" % "\n  ".join(broken))
    return out, broken
