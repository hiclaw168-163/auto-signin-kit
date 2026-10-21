"""Trae 离线体检: 登录态可读性与有效期;不联网、不领取。"""

from __future__ import annotations

from auto_signin_kit.domains.trae.api import load_session_state
from auto_signin_kit.domains.trae.auth import NoAuthError, TraeAuthError, token_expired


def run_doctor(override_dir: str | None = None) -> tuple[dict, int]:
    """产出 ``doctor`` 结果;退出码 0/1/2 由 result 决定。"""
    out: dict = {
        "result": "OK",
        "needs_attention": False,
        "token_source": "env",
        "online_checked": False,
        "report": "使用环境变量 TRAE_TOKEN（无法离线校验有效期）",
    }
    import os

    if os.environ.get("TRAE_TOKEN"):
        return out, 0

    out["token_source"] = "desktop"
    try:
        info = load_session_state(override_dir)
    except NoAuthError as exc:
        out.update({"result": "NO_AUTH", "needs_attention": True, "report": str(exc),
                    "looked_in": exc.looked_in or None})
        return out, 2
    except TraeAuthError as exc:
        out.update({"result": "AUTH_ERROR", "needs_attention": True, "report": str(exc),
                    "looked_in": exc.looked_in or None})
        return out, 1

    out.update({
        "storage": info.get("_storage"),
        "token_expires_at": info.get("expiredAt"),
        "user_id": info.get("userId"),
        "username": (info.get("account", {}).get("username")
                     if isinstance(info.get("account"), dict) else None),
        "decrypted": True,
    })

    if token_expired(info):
        out.update({"result": "AUTH_REJECTED", "needs_attention": True,
                    "report": "登录态已于 %s 过期，请打开 Trae CN 桌面端重新登录"
                              % info.get("expiredAt")})
        return out, 1

    expires_text = str(info.get("expiredAt") or "")[:16]
    report = "登录态可读" + ("，有效期至 %s" % expires_text if expires_text else "")
    out["report"] = report
    return out, 0
