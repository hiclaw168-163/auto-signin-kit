"""Qoder 离线体检: 登录态可读性 + 设备组件发现;不联网、不领取。"""

from __future__ import annotations

from auto_signin_kit.core.kit.tokens import parse_iso
from auto_signin_kit.domains.client_signin.apps.qoder.api import load_session_state
from auto_signin_kit.domains.client_signin.apps.qoder.auth import (
    TZ_CN,
    NoAuthError,
    QoderAuthError,
    token_expired,
)
from auto_signin_kit.domains.client_signin.apps.qoder.profile import INTL, QoderProfile


def run_doctor(auth_dir: str | None = None, *, keychain_service: str = "",
               profile: QoderProfile = INTL) -> tuple[dict, int]:
    """产出 ``doctor`` 结果;退出码 0/1/2 由 result 决定。

    ``auth_dir``(SPEC-12 P2): 具名账户的显式数据目录;给出时环境变量令牌
    不参与(体检的就是这个账户的登录态)。
    ``keychain_service``(mac): 该账户的 Keychain 条目名,透传给登录态解析。
    ``profile``: 客户端身份(体检哪个客户端的登录态与令牌变量)。
    """
    out: dict = {
        "result": "OK",
        "needs_attention": False,
        "token_source": "env",
        "online_checked": False,
        "report": "使用环境变量 %s（无法离线校验有效期）" % profile.env("TOKEN"),
    }
    import os

    if auth_dir is None and os.environ.get(profile.env("TOKEN")):
        return out, 0

    out["token_source"] = "desktop"
    try:
        sess = load_session_state(auth_dir, keychain_service=keychain_service,
                                  profile=profile)
    except NoAuthError as exc:
        out.update({
            "result": "NO_AUTH", "needs_attention": True, "report": str(exc),
            "looked_in": exc.looked_in or None,
        })
        return out, 2
    except QoderAuthError as exc:
        out.update({
            "result": "AUTH_ERROR", "needs_attention": True, "report": str(exc),
            "looked_in": exc.looked_in or None,
        })
        return out, 1

    from auto_signin_kit.domains.client_signin.apps.qoder.risk import (
        find_device_component,
        risk_headers,
    )

    out.update({
        "auth_dir": sess.get("_auth_dir"),
        "token_expires_at": sess.get("expiresAt"),
        "decrypted": True,
        "device_component": find_device_component(),
    })
    out["device_identity"] = bool(risk_headers(sess.get("_auth_dir")).get("Cosy-MachineToken"))

    if token_expired(sess):
        out.update({
            "result": "AUTH_REJECTED", "needs_attention": True,
            "report": "登录态已于 %s 过期，请打开 Qoder CN 桌面端重新登录" % sess.get("expiresAt"),
        })
        return out, 1

    expires = parse_iso(sess.get("expiresAt"))
    expire_text = expires.astimezone(TZ_CN).strftime("%Y-%m-%d %H:%M") if expires else ""
    report = "登录态可读" + ("，有效期至 %s" % expire_text if expire_text else "")
    if not out["device_identity"]:
        report += "；未取到设备标识，查询仍会尝试（通常不影响）"
    out["report"] = report
    return out, 0
