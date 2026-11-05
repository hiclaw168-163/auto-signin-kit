"""web_signin 认证适配器: YAML ``auth.kind`` → 凭据解析的注册表。

为什么是适配器: 登录态的形态真实地有两种以上——普通 JSON 令牌文件
(``plain``)与 Trae 桌面端 SS_AES 加密信封(``ss_aes``,见 ss_aes 模块),
且以后还会出现新的形态;这正是 AGENTS 规则 5 "真的需要第二种实现"
的判定情形。注册表把"凭证怎么读"关在适配器里,引擎流程只消费
:class:`Credential`(令牌 + 请求头 + 有效期)。

适配器契约(模块级函数,按 kind 查 :data:`ADAPTERS`):
``resolve(decl, ref) -> Credential`` / ``doctor(decl, ref) -> (dict, int)``
/ ``ready(decl) -> bool``;``ref`` 是具名账户的 AccountRef 或 None(default)。
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.kit.domain import DomainError
from auto_signin_kit.core.kit.tokens import parse_iso
from auto_signin_kit.domains.web_signin.schema import AppDeclaration


@dataclass
class Credential:
    """解析产物: 引擎请求层只看这里,不关心登录态文件长什么样。"""

    """解析产物: 引擎请求层只看这里,不关心登录态文件长什么样。"""

    token: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    expires_at: Any = None  # 原始值(通常 ISO 串),供台账展示
    source: str = ""  # 诊断: 凭据文件/目录路径,不含令牌


class Adapter(Protocol):
    """适配器需提供的三个函数(结构化类型,不强制继承)。"""

    def resolve(self, decl: AppDeclaration, ref: Any) -> Credential:
        """凭据解析: 失败按语义抛 NoAuthError/AuthBrokenError/AuthRejectedError。"""
        ...

    def doctor(self, decl: AppDeclaration, ref: Any) -> tuple[dict, int]:
        """离线体检登录态可读性与有效期;返回 (RunOutcome, 退出码)。"""
        ...

    def ready(self, decl: AppDeclaration) -> bool:
        """就绪探测(应用同步/调度展示): 不解锁凭据内容。"""
        ...


def dig(data: Any, dotted: str) -> Any:
    """按点路径取值;数字段取列表下标;缺路径返回 None(不抛)。"""
    cur = data
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def expired(value: Any) -> bool:
    """有效期字段已过期(在过去);缺字段/解析不了视为未过期。"""
    moment = parse_iso(value)
    from datetime import UTC, datetime

    return moment is not None and moment <= datetime.now(UTC)


def expand_files(patterns: list[str]) -> list[Path]:
    """展开候选文件 glob(~ 展开);返回存在的文件,保持声明顺序。"""
    out: list[Path] = []
    for pattern in patterns:
        expanded = str(Path(pattern).expanduser())
        matches = [Path(p) for p in glob.glob(expanded)]
        out.extend(p for p in sorted(matches) if p.is_file() and p not in out)
    return out


class AuthError(AutoSignInKitError):
    """认证层错误的基类;looked_in 列出探测过的路径(不含令牌)。"""

    def __init__(self, message: str, looked_in: list[str] | None = None) -> None:
        """message 直接可读;looked_in 列出探测过的路径供 extra 上报。"""
        super().__init__(message)
        self.looked_in = list(looked_in or [])

    def to_domain_error(self) -> DomainError:
        """转成引擎统一出口: 子类决定 result 与退出码。"""
        raise NotImplementedError


class NoAuthError(AuthError):
    """本机没有可用登录态 → NO_AUTH,退出码 2。"""

    exit_code = 2

    def to_domain_error(self) -> DomainError:
        """NO_AUTH,退出码 2(引导先登录,不算故障)。"""
        return DomainError("NO_AUTH", str(self), exit_code=2,
                           extra={"looked_in": self.looked_in or None})


class AuthBrokenError(AuthError):
    """登录态存在但读不出令牌 → AUTH_ERROR,退出码 1。"""

    def to_domain_error(self) -> DomainError:
        """AUTH_ERROR,退出码 1(需要人工介入修复凭据)。"""
        return DomainError("AUTH_ERROR", str(self), exit_code=1,
                           extra={"looked_in": self.looked_in or None})


class AuthRejectedError(AuthError):
    """登录态已过期 → AUTH_REJECTED,退出码 1。"""

    def to_domain_error(self) -> DomainError:
        """AUTH_REJECTED,退出码 1(服务端明确拒绝,重新登录)。"""
        return DomainError("AUTH_REJECTED", str(self), exit_code=1,
                           extra={"looked_in": self.looked_in or None})


def _use_env(decl: AppDeclaration, ref: Any, skip_env: bool) -> bool:
    """环境变量令牌只属于 default 账户;具名账户一律走显式凭据。"""
    if skip_env or not decl.auth.env:
        return False
    return ref is None or ref.name == "default"


def resolve(decl: AppDeclaration, ref: Any,
            *, skip_env: bool = False) -> Credential:
    """凭据解析统一入口: env 令牌优先(default 户)→ 按 kind 分发适配器。

    ``ref``: AccountRef(default 未声明时 None);具名账户一律走适配器
    显式路径并隔离环境变量令牌——那是 default 户的。
    """
    if _use_env(decl, ref, skip_env) and (os.environ.get(decl.auth.env) or "").strip():
        token = os.environ[decl.auth.env].strip()
        headers = _token_headers(decl, token)
        return Credential(token=token, headers=headers, source="env:" + decl.auth.env)
    adapter = ADAPTERS.get(decl.auth.kind)
    if adapter is None:
        raise ConfigKindError(decl.auth.kind)
    return adapter.resolve(decl, ref)


class ConfigKindError(AutoSignInKitError):
    """auth.kind 没有对应适配器;列出当前已注册的 kind。"""

    def __init__(self, kind: str) -> None:
        """报错时列出当前已注册的 kind,给拼写纠错的出路。"""
        super().__init__("auth.kind=%r 没有对应适配器(可用: %s)"
                         % (kind, ", ".join(sorted(ADAPTERS))))


def _token_headers(decl: AppDeclaration, token: str) -> dict[str, str]:
    header = decl.auth.token_header
    if header is None or not token:
        return {}
    return {header.name: header.prefix + token}


def doctor(decl: AppDeclaration, ref: Any, *, skip_env: bool = False) -> tuple[dict, int]:
    """体检统一入口: env 命中即报 OK(无法离线校验),否则按 kind 分发。"""
    if _use_env(decl, ref, skip_env) and os.environ.get(decl.auth.env or ""):
        return {"result": "OK", "needs_attention": False, "token_source": "env",
                "online_checked": False,
                "report": "使用环境变量 %s（无法离线校验有效期）" % decl.auth.env}, 0
    adapter = ADAPTERS.get(decl.auth.kind)
    if adapter is None:
        raise ConfigKindError(decl.auth.kind)
    return adapter.doctor(decl, ref)


def ready(decl: AppDeclaration) -> bool:
    """就绪探测(应用同步/调度展示用): 按 kind 分发;探测异常视为未就绪。"""
    try:
        adapter = ADAPTERS.get(decl.auth.kind)
        return bool(adapter.ready(decl)) if adapter else False
    except Exception:
        return False


def _read_json_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuthBrokenError("%s: %s: %s" % (path.name, type(exc).__name__, exc)) from exc
    return data if isinstance(data, dict) else {}


#%% ---------------- plain: 普通 JSON 令牌文件 ----------------

def _plain_resolve(decl: AppDeclaration, ref: Any) -> Credential:
    """JSON 令牌文件: 候选 glob → 首个存在的文件 → 点路径取令牌。"""
    patterns = [ref.auth_file] if (ref is not None and ref.auth_file) else decl.auth.files
    if not patterns:
        raise NoAuthError("auth.files 未声明,且账户也未给 auth_file")
    looked_in = [str(Path(p).expanduser()) for p in patterns]
    candidates = expand_files(patterns)
    if not candidates:
        raise NoAuthError("没有找到登录态文件（探测: %s）" % ", ".join(looked_in), looked_in)
    path = candidates[0]
    data = _read_json_file(path)
    token = dig(data, decl.auth.token_json_path or "token")
    if not isinstance(token, str) or not token.strip():
        raise AuthBrokenError(
            "%s 里 %s 取不到令牌，请确认 token_json_path 或重新登录"
            % (path.name, decl.auth.token_json_path), looked_in)
    token = token.strip()
    expires_at = (dig(data, decl.auth.expires_json_path)
                  if decl.auth.expires_json_path else None)
    if expired(expires_at):
        raise AuthRejectedError("登录态已于 %s 过期，请重新登录" % expires_at, looked_in)
    headers = _token_headers(decl, token)
    headers.update(decl.auth.headers)
    return Credential(token=token, headers=headers, expires_at=expires_at,
                      source=str(path))


def _plain_doctor(decl: AppDeclaration, ref: Any) -> tuple[dict, int]:
    try:
        cred = _plain_resolve(decl, ref)
    except NoAuthError as exc:
        return {"result": "NO_AUTH", "needs_attention": True, "report": str(exc),
                "looked_in": exc.looked_in or None}, 2
    except AuthError as exc:
        return {"result": "AUTH_ERROR", "needs_attention": True, "report": str(exc),
                "looked_in": exc.looked_in or None}, 1
    expires_text = str(cred.expires_at or "")[:16]
    report = "登录态可读（%s）" % cred.source
    if expires_text:
        report += "，有效期至 %s" % expires_text
    return {"result": "OK", "needs_attention": False, "token_source": "file",
            "online_checked": False, "source": cred.source,
            "token_expires_at": cred.expires_at, "report": report}, 0


def _plain_ready(decl: AppDeclaration) -> bool:
    return bool(expand_files(decl.auth.files))


#%% ---------------- plain 适配器(对象壳: 三个契约函数挂同名静态方法) ----------------

class PlainAdapter:
    """普通 JSON 令牌文件适配器。"""

    resolve = staticmethod(_plain_resolve)
    doctor = staticmethod(_plain_doctor)
    ready = staticmethod(_plain_ready)


#%% ---------------- 注册表 ----------------

def _ss_aes_module() -> Any:
    """延迟导入: ss_aes 只在 kind 命中时加载(cryptography 依赖隔离)。"""
    from auto_signin_kit.domains.web_signin import ss_aes

    return ss_aes


def _chromium_module() -> Any:
    """延迟导入: chromium_cookies 客户端凭据适配器(kind 命中时加载)。"""
    from auto_signin_kit.domains.web_signin import chromium

    return chromium


ADAPTERS: dict[str, Any] = {
    "plain": PlainAdapter(),
    "ss_aes": _ss_aes_module(),
    "chromium_cookies": _chromium_module(),
}
