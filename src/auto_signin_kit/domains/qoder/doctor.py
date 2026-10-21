"""Qoder 离线体检: 登录态可读性 + 设备组件发现;不联网、不领取。"""

from __future__ import annotations

from auto_signin_kit.domains.qoder.api import load_session_state
from auto_signin_kit.domains.qoder.auth import (
    TZ_CN,
    NoAuthError,
    QoderAuthError,
    _parse_iso,
    token_expired,
)


def run_doctor() -> tuple[dict, int]:
    """产出 ``doctor`` 结果;退出码 0/1/2 由 result 决定。"""
    out: dict = {
        "result": "OK",
        "needs_attention": False,
        "token_source": "env",
        "online_checked": False,
        "report": "使用环境变量 QODER_TOKEN（无法离线校验有效期）",
    }
    import os

    if os.environ.get("QODER_TOKEN"):
        return out, 0

    out["token_source"] = "desktop"
    try:
        sess = load_session_state()
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

    from auto_signin_kit.domains.qoder.risk import find_device_component, risk_headers

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

    expires = _parse_iso(sess.get("expiresAt"))
    expire_text = expires.astimezone(TZ_CN).strftime("%Y-%m-%d %H:%M") if expires else ""
    report = "登录态可读" + ("，有效期至 %s" % expire_text if expire_text else "")
    if not out["device_identity"]:
        report += "；未取到设备标识，查询仍会尝试（通常不影响）"
    out["report"] = report
    return out, 0
