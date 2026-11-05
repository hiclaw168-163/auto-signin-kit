"""ask_apps: 应用管理态的读查与维护(应用总开关/展示名覆盖/备注)。

设计边界(见 data/sql/ask_apps.sql 头注释): 域的存在性跟代码走
(entry_points 注册表 + 域 YAML),本表只承载运行期管理态。行是**惰性
创建**的——注册表里有哪些域由调用方(ui 层,能同时看到注册表与本层)
驱动 :func:`ensure_app` 补齐,core 层不反向感知注册表。

enabled 语义压在账户级 enabled 之上: 静默(调度)触发时关掉的域整个
跳过(不执行、不落库);手动执行永远放行——总开关管的是"别再自动跑",
不是"禁止人碰"。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.store.connection import connect


def app_state(domain: str, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """单应用管理态行;从未操作过的应用返回 None(视为启用、用注册表展示名)。"""
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute(
            "SELECT id, domain, enabled, display, note, created_at, updated_at"
            " FROM ask_apps WHERE domain = ?", (domain,)).fetchone()
    finally:
        if own:
            conn.close()
    return dict(row) if row else None


def ensure_app(domain: str, conn: sqlite3.Connection | None = None) -> None:
    """惰性补齐管理态行(已存在则原样返回);调用方负责传注册表里的真实域名。"""
    own = conn is None
    conn = conn or connect()
    try:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO ask_apps (domain) VALUES (?)", (domain,))
    finally:
        if own:
            conn.close()


def app_enabled(domain: str) -> bool:
    """应用总开关;从未操作过的应用(无行)视为启用(惰性创建,不挡路)。"""
    state = app_state(domain)
    return bool(state["enabled"]) if state else True


def set_app_state(domain: str, *, enabled: bool | None = None,
                  display: str | None = None, note: str | None = None) -> dict[str, Any]:
    """更新管理态字段(部分更新);行不存在先补齐,返回更新后的整行。

    ``display``/``note`` 传 None 表示不改;要清空传空串(显式动作)。
    """
    sets = ["updated_at = strftime('%Y-%m-%d %H:%M:%S','now','localtime')"]
    params: list[Any] = []
    if enabled is not None:
        sets.append("enabled = ?")
        params.append(1 if enabled else 0)
    if display is not None:
        sets.append("display = ?")
        params.append(display)
    if note is not None:
        sets.append("note = ?")
        params.append(note)
    conn: sqlite3.Connection | None = None
    try:
        conn = connect()
        ensure_app(domain, conn)
        with conn:
            # S608 豁免: 列名来自上面的固定字段白名单,无外部输入
            conn.execute("UPDATE ask_apps SET %s WHERE domain = ?"  # noqa: S608
                         % ", ".join(sets), (*params, domain))
        row = conn.execute(
            "SELECT id, domain, enabled, display, note, created_at, updated_at"
            " FROM ask_apps WHERE domain = ?", (domain,)).fetchone()
    except sqlite3.Error as exc:  # pragma: no cover - sqlite 层异常透传语义
        raise AutoSignInKitError("应用管理态更新失败: %s" % exc) from exc
    finally:
        if conn is not None:
            conn.close()
    return dict(row)
