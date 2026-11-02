"""域配置的读取/校验/保存 API: YAML 是唯一事实源,UI 只做安全的读写代理。

边界(SPEC-09):
- 保存必须过各域 pydantic schema 校验(与 CLI 同一套),配错字段在保存时
  就精确报 loc/msg,不让坏配置溜进文件;
- 写入用临时文件 + ``os.replace`` 原子替换,写一半断电不会留下半个配置;
- 只改各域**主 YAML**;``<域>.local.yaml`` 本机覆盖不在 UI 里编辑(它
  gitignore 不入库,内容往往含本机路径,表单化编辑风险大)。
"""

from __future__ import annotations

import importlib
import os
from importlib.metadata import entry_points
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from auto_signin_kit.core.config import resolve_config_dir
from auto_signin_kit.core.config.schema import DomainConfig
from auto_signin_kit.core.errors import ConfigError

router = APIRouter()


class ConfigBody(BaseModel):
    """PUT /config/{domain} 的请求体: 整份 YAML 文本。"""

    content: str


def _known_domains() -> list[str]:
    return sorted(ep.name for ep in entry_points(group="auto_signin_kit.domains"))


def _schema_cls(domain: str) -> type[DomainConfig]:
    """域名 → 该域的 pydantic 配置类;无 schema 的域不可经 UI 编辑。"""
    try:
        module = importlib.import_module(f"auto_signin_kit.domains.{domain}.schema")
    except ModuleNotFoundError as exc:
        raise HTTPException(404, f"未知域: {domain}") from exc
    for obj in vars(module).values():
        if (isinstance(obj, type) and issubclass(obj, DomainConfig)
                and obj is not DomainConfig):
            return obj
    raise HTTPException(404, f"域 {domain} 没有可编辑的配置 schema")


def _yaml_path(domain: str) -> Any:
    try:
        config_dir = resolve_config_dir()
    except ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    return config_dir / "domains" / f"{domain}.yaml"


def _read(domain: str) -> str:
    path = _yaml_path(domain)
    if not path.exists():
        raise HTTPException(404, f"配置不存在: {path.name}(可从 config/examples 复制)")
    return path.read_text(encoding="utf-8")


def _validate(domain: str, content: str) -> DomainConfig:
    """YAML 文本 → pydantic 模型;格式错/字段错一律 422 + 精确位置。"""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise HTTPException(422, f"YAML 格式错误: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(422, "YAML 顶层必须是映射(key: value)")
    try:
        return _schema_cls(domain).model_validate(data)
    except ValidationError as exc:
        errors = [
            {"loc": ".".join(str(p) for p in err["loc"]), "msg": err["msg"]}
            for err in exc.errors()
        ]
        raise HTTPException(422, {"message": "配置校验失败", "errors": errors}) from exc


@router.get("/config")
def list_configs() -> list[dict[str, Any]]:
    """各域配置的存在情况(按 entry_points 全量列出,与 CLI 同源)。"""
    return [
        {"domain": domain, "exists": _yaml_path(domain).exists()}
        for domain in _known_domains()
    ]


@router.get("/config/{domain}")
def get_config(domain: str) -> dict[str, Any]:
    """读域配置原文(YAML 文本,编辑器直接展示)。"""
    return {"domain": domain, "content": _read(domain)}


@router.post("/config/{domain}/validate")
def validate_config(domain: str, body: ConfigBody) -> dict[str, Any]:
    """只校验不落盘;返回 ok=true 或 422(带逐字段错误)。"""
    _validate(domain, body.content)
    return {"ok": True}


@router.put("/config/{domain}")
def save_config(domain: str, body: ConfigBody) -> dict[str, Any]:
    """校验通过后原子写回主 YAML;生效时机 = 下次对应命令运行。"""
    if domain not in _known_domains():
        raise HTTPException(404, f"未知域: {domain}")
    _validate(domain, body.content)
    path = _yaml_path(domain)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(body.content, encoding="utf-8")
    os.replace(tmp, path)  # 原子替换: 不会留下写了一半的配置
    return {"ok": True, "path": str(path)}
