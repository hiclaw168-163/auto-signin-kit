"""WorkBuddy 登录会话: 校验、鉴权头、明文/加密凭据解析。

凭据文件由桌面端登录后自动写出;旧版 accessToken 是明文,新版可能为
``$wbEncrypted`` 信封——后者交给客户端自带运行时在隐藏子进程里解密,
只经内存管道返回 access token,不处理 refresh token(登录与续期仍由客户端负责)。
"""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
from typing import Any

from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.httpclient import Budget
from auto_signin_kit.domains.workbuddy.helper_js import AUTH_HELPER_JS

#: 与凭据助手子进程交互的超时与管道上限。
AUTH_HELPER_TIMEOUT = 10.0
AUTH_INPUT_LIMIT = 65536
AUTH_OUTPUT_LIMIT = 65536
TOKEN_LIMIT = 32768

DEFAULT_ENDPOINT = "https://copilot.tencent.com"

AUTH_REASONS = {
    "INVALID_FORMAT": "登录凭据格式无效，请检查凭据来源和客户端版本",
    "MISSING_SESSION": "本地缺少登录会话或必要字段，请先登录客户端",
    "UNSUPPORTED_ENVELOPE": "尚不支持此加密凭据格式，请更新脚本；重新登录不会改变加密格式",
    "RUNTIME_NOT_FOUND": "未找到 WorkBuddy 客户端，请用 WORKBUDDY_EXE 指定其可执行文件",
    "INVALID_RUNTIME_PATH": "WORKBUDDY_EXE 不是可用的可执行文件，请核对路径",
    "RUNTIME_UNAVAILABLE": "客户端运行时不支持所需的原生存储接口，请检查客户端和脚本版本",
    "KEY_MISMATCH": "凭据与所选客户端的密钥不匹配，请用 WORKBUDDY_EXE 指定对应客户端",
    "DECRYPT_FAILED": "加密凭据认证失败，请检查客户端版本及凭据是否完整",
    "HELPER_TIMEOUT": "凭据处理超时，已停止子进程，请稍后重试",
    "HELPER_PROTOCOL": "客户端凭据助手返回无效结果，请检查客户端和脚本版本",
}

#: 只允许出现这些非敏感 message 固定值；token 一律不进异常文本。
_ALLOWED_HELPER_REASONS = frozenset(
    {
        "INVALID_FORMAT", "UNSUPPORTED_ENVELOPE", "RUNTIME_UNAVAILABLE",
        "KEY_MISMATCH", "DECRYPT_FAILED", "HELPER_TIMEOUT", "HELPER_PROTOCOL",
    }
)


#: 跨模块共享的敏感值登记表;emit 前统一在 JSON 文本里替换为 [REDACTED]。
SENSITIVE: set[str] = set()


class AuthError(AutoSignInKitError):
    """凭据层失败;只有固定、非敏感的 message 跨越凭据边界。"""

    def __init__(self, reason: str) -> None:
        """message 只取固定的人话文案,稳定类别存到 reason。"""
        super().__init__(AUTH_REASONS.get(reason, "本地未找到有效登录会话，请先登录客户端"))
        self.reason = reason

    def output(self) -> dict[str, Any]:
        """转成结果 dto(结果码 AUTH_ERROR,需人工介入)。"""
        return {
            "result": "AUTH_ERROR",
            "reason": self.reason,
            "report": str(self),
            "needs_attention": True,
        }


def _valid_token(token: object) -> bool:
    return (
        isinstance(token, str)
        and 0 < len(token) <= TOKEN_LIMIT
        and re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token) is not None
    )


def _validate_session(session: Any) -> None:
    """结构与格式双重校验;合法字段之外的任何形状都以 INVALID_FORMAT 拒绝。"""
    if not isinstance(session, dict):
        raise AuthError("INVALID_FORMAT")
    for field in ("auth", "account"):
        if session.get(field) is None:
            raise AuthError("MISSING_SESSION")
        if not isinstance(session[field], dict):
            raise AuthError("INVALID_FORMAT")
    auth = session["auth"]
    token_attr = auth.get("accessToken")
    uid = session["account"].get("uid")
    if token_attr in (None, "") or uid in (None, ""):
        raise AuthError("MISSING_SESSION")
    for source, fields in (
        (session["account"], ("uid", "enterpriseId")),
        (session["auth"], ("domain",)),
    ):
        for field in fields:
            value = source.get(field)
            if value not in (None, "") and (
                not isinstance(value, str)
                or re.fullmatch(r"[\x21-\x7e]{1,2048}", value) is None
            ):
                raise AuthError("INVALID_FORMAT")
    endpoint = session["auth"].get("endpoint")
    if endpoint in (None, ""):
        return
    if not isinstance(endpoint, str) or re.search(r"[\s\x00-\x1f\x7f]", endpoint):
        raise AuthError("INVALID_FORMAT")
    try:
        parsed = urllib.parse.urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError()
        _port = parsed.port  # 显式端口在这里完成合法性验证,不打日志。
    except ValueError:
        raise AuthError("INVALID_FORMAT") from None


def _token_format(value: Any) -> str:
    """识别 token 形态: "plaintext"(明文字符串) 或 "sym-v1"(受支持的信封)。"""
    if isinstance(value, str):
        if not _valid_token(value):
            raise AuthError("INVALID_FORMAT")
        return "plaintext"
    if not isinstance(value, dict):
        raise AuthError("INVALID_FORMAT")
    if (
        set(value) != {"$wbEncrypted", "envelope"}
        or type(value.get("$wbEncrypted")) is not int
        or int(value["$wbEncrypted"]) != 1
    ):
        raise AuthError("UNSUPPORTED_ENVELOPE")
    try:
        encoded = value["envelope"]
        if not isinstance(encoded, str) or not 0 < len(encoded) <= AUTH_INPUT_LIMIT - 1024:
            raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        if base64.b64encode(raw).decode("ascii") != encoded:
            raise ValueError()
        envelope = json_loads(raw)
        if not isinstance(envelope, dict) or type(envelope.get("suite")) is not int:
            raise ValueError()
        if envelope["suite"] != 1:
            raise AuthError("UNSUPPORTED_ENVELOPE")
        if set(envelope) != {"suite", "keyId", "nonce", "authTag", "ciphertext"}:
            raise ValueError()
        if not isinstance(envelope["keyId"], str) or re.fullmatch(
            r"[0-9a-f]{16}", envelope["keyId"]
        ) is None:
            raise ValueError()
        for field, length in (("nonce", 12), ("authTag", 16), ("ciphertext", None)):
            data = envelope[field]
            if not isinstance(data, str):
                raise ValueError()
            decoded = base64.b64decode(data, validate=True)
            if base64.b64encode(decoded).decode("ascii") != data or (
                length is not None and len(decoded) != length
            ):
                raise ValueError()
        return "sym-v1"
    except (ValueError, TypeError, KeyError):
        raise AuthError("INVALID_FORMAT") from None


def json_loads(raw: bytes) -> Any:
    """从字节流解析 JSON。"""
    return json.loads(raw.decode("utf-8"))


def build_headers(session: dict) -> dict[str, str]:
    """按会话构造 API 鉴权头;token 永不进日志或异常文本。"""
    _validate_session(session)
    auth = session.get("auth") or {}
    account = session.get("account") or {}
    token = auth.get("accessToken")
    uid = account.get("uid")
    if not _valid_token(token):
        raise AuthError("INVALID_FORMAT")
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer %s" % token,
        "Content-Type": "application/json",
        "X-User-Id": uid,
        "User-Agent": "WorkBuddy",
    }
    if account.get("enterpriseId"):
        headers["X-Enterprise-Id"] = account["enterpriseId"]
        headers["X-Tenant-Id"] = account["enterpriseId"]
    if auth.get("domain"):
        headers["X-Domain"] = auth["domain"]
    return headers


def _helper_env(base: dict[str, str]) -> dict[str, str]:
    """助手子进程环境: 剥离可能干扰的变量,并以 Node 模式运行 Electron。"""
    env = {
        key: value
        for key, value in base.items()
        if not key.upper().startswith(("NODE_", "ELECTRON_", "WORKBUDDY_", "AUTO_SIGNIN_KIT_"))
    }
    env["ELECTRON_RUN_AS_NODE"] = "1"
    return env


def run_helper(exe: str, request: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """跑一次凭据助手;bounded pipes + 有硬超时,失败统一转 AuthError。"""
    from auto_signin_kit.core.adapters import native_helper

    timeout = min(AUTH_HELPER_TIMEOUT, budget.remaining())
    try:
        return native_helper.run_json_stdin_helper(
            exe,
            AUTH_HELPER_JS,
            request,
            timeout=timeout,
            allowed_reasons=_ALLOWED_HELPER_REASONS,
            env_filter=_helper_env,
        )
    except native_helper.NativeHelperError as exc:
        raise AuthError(exc.reason if exc.reason in AUTH_REASONS else "HELPER_PROTOCOL") from None


def resolve_session(session: dict, budget: Budget) -> dict:
    """校验会话并把加密 token 解成明文;明文 token 会登记为敏感值用于脱敏。"""
    from auto_signin_kit.domains.workbuddy import runtime as runtime_mod

    _validate_session(session)
    token = session["auth"]["accessToken"]
    if _token_format(token) != "plaintext":
        exe = runtime_mod.find_workbuddy_runtime(budget)
        reply = run_helper(exe, {"operation": "decrypt", "value": token}, budget)
        if set(reply) != {"version", "ok", "accessToken"} or not _valid_token(
            reply.get("accessToken")
        ):
            raise AuthError("HELPER_PROTOCOL")
        token = reply["accessToken"]
    SENSITIVE.add(token)
    return dict(session, auth=dict(session["auth"], accessToken=token))
