"""配置加载: 域 YAML 唯一配置源。

**这个模块里最重要的一条规则,写在最前面**: ``resolve_config_dir()`` 必须是
一个函数,在用到的地方才调用,绝对不能在模块顶层写成
``CONFIG_DIR = resolve_config_dir()`` 这样的模块级常量。

原因: Python 模块只在第一次 import 时执行一次,如果把配置目录解析结果
缓存成模块级常量,那么测试里用 ``monkeypatch.setenv("AUTO_SIGNIN_KIT_CONFIG_DIR", tmp_path)``
想要重定向配置目录,会因为模块早就 import 过、常量早就冻结了而完全不生效——
这个坑真实发生过,不是假设的风险。正确的隔离测试方式是
``monkeypatch.setattr(config_module, "resolve_config_dir", lambda: tmp_path)``
或者让 ``resolve_config_dir()`` 保持惰性,每次都重新读一遍环境变量。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import ValidationError

from auto_signin_kit.core.config.schema import DomainConfig
from auto_signin_kit.core.envvar import auto_signin_kit_env
from auto_signin_kit.core.errors import ConfigError

TDomainConfig = TypeVar("TDomainConfig", bound=DomainConfig)


def resolve_config_dir() -> Path:
    """解析配置目录(使用处惰性调用,import 本模块无副作用)。

    优先级:
    1. 环境变量 ``AUTO_SIGNIN_KIT_CONFIG_DIR``(必须是已存在目录)
    2. 包内 ``auto_signin_kit/config``(wheel 分发布局)
    3. 仓库根 ``config/``(editable 安装布局)
    """
    override = auto_signin_kit_env("CONFIG_DIR")
    if override:
        path = Path(override).expanduser().resolve()
        if not path.is_dir():
            raise ConfigError(f"AUTO_SIGNIN_KIT_CONFIG_DIR 不是有效目录: {path}")
        return path

    pkg_root = Path(__file__).resolve().parents[2]  # …/auto_signin_kit
    for candidate in (pkg_root / "config", pkg_root.parent.parent / "config"):
        if candidate.is_dir():
            return candidate

    raise ConfigError(
        "找不到 config 目录。"
        f"请设置 AUTO_SIGNIN_KIT_CONFIG_DIR,或使用 editable 安装。"
        f"(尝试路径: {pkg_root / 'config'})"
    )


def _domain_yaml_path(domain: str) -> Path:
    return resolve_config_dir() / "domains" / f"{domain}.yaml"


def _read_domain_dict(domain: str) -> tuple[Path, dict[str, Any]]:
    """读域 YAML 为 dict;不存在/格式错立即 ConfigError,不做静默兜底。"""
    path = _domain_yaml_path(domain)
    if not path.exists():
        example = resolve_config_dir() / "examples" / f"{domain}.yaml.example"
        hint = f"cp {example} {path}" if example.exists() else f"create {path}"
        raise ConfigError(f"配置不存在: {path} — {hint}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"配置格式错误: {path} — {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"配置格式错误(应为映射): {path}")
    return path, data


def _validation_error(path: Path, exc: ValidationError) -> ConfigError:
    problems = "; ".join(
        f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
    )
    return ConfigError(f"配置校验失败: {path} — {problems}")


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """递归合并: overlay 覆盖 base;两侧同键都是映射时深入合并,其余类型直接覆盖。"""
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_domain_config_model(
    domain: str,
    schema_cls: type[TDomainConfig],
    *,
    merge_local: bool = False,
) -> TDomainConfig:
    """读取并校验域配置,返回 pydantic 模型。字段拼错/类型错在这里精确报错,
    不是等运行到某一行才发现。

    ``merge_local=True`` 时叠加同目录 ``<域>.local.yaml`` 做深合并——本机差异
    (路径/容器名/服务名)写这个文件(gitignore,不入库),主 YAML 保持可入库;
    映射键递归合并、标量直接覆盖。local 文件不存在则原样通过:
    没有本机差异是常态,不是错误。
    """
    path, data = _read_domain_dict(domain)
    if merge_local:
        local_path = resolve_config_dir() / "domains" / f"{domain}.local.yaml"
        if local_path.exists():
            try:
                overlay = yaml.safe_load(local_path.read_text(encoding="utf-8"))
            except yaml.YAMLError as e:
                raise ConfigError(f"配置格式错误: {local_path} — {e}") from e
            if overlay is not None:
                if not isinstance(overlay, dict):
                    raise ConfigError(f"配置格式错误(应为映射): {local_path}")
                data = _deep_merge(data, overlay)
    try:
        return schema_cls.model_validate(data)
    except ValidationError as e:
        raise _validation_error(path, e) from e
