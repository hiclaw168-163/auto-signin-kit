"""ask_apps: 应用主档的读查与维护(总开关/版本区/显示名/描述)。

设计边界(见 data/sql/ask_apps.sql 头注释): 域的存在性跟代码走
(entry_points 注册表 + 域 YAML),本表只承载管理态与档案。行是**惰性
创建**的——注册表里有哪些应用由调用方(ui 层,能同时看到注册表与本层)
驱动 :func:`ensure_app` 补齐,core 层不反向感知注册表。

关联约定: ``ask_accounts.domain`` / ``ask_signin_runs.domain`` 的列值
即本表 ``app_code``(注册表域名),全库按它 join。

enabled 语义压在账户级 enabled 之上: 静默(调度)触发时关掉的应用整个
跳过(不执行、不落库);手动执行永远放行——总开关管的是"别再自动跑",
不是"禁止人碰"。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.store.connection import connect

_ROW_COLS = "id, app_code, region, enabled, app_name, app_desc, created_at, updated_at"


def app_state(app_code: str, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """单应用主档行;从未操作过的应用返回 None(视为启用、用注册表展示名)。"""
    own = conn is None
    conn = conn or connect()
    try:
        # S608 豁免: 列名是本模块固定白名单 _ROW_COLS,无外部输入
        row = conn.execute(
            "SELECT %s FROM ask_apps WHERE app_code = ?" % _ROW_COLS,  # noqa: S608
            (app_code,)).fetchone()
    finally:
        if own:
            conn.close()
    return dict(row) if row else None


def ensure_app(app_code: str, region: str = "cn",
               conn: sqlite3.Connection | None = None) -> None:
    """惰性补齐主档行;已存在的行若 region 与探测结论不符则就地纠正。

    region 来源是同步时的本机探测(如 qoder 按数据目录名判 cn/intl),
    比历史行可信——比如先在机器上登记过 CN 版、后来换成国际版客户端,
    下次同步应把行迁到 intl 而不是留着错值(当前契约: 每应用一行;
    真·双版本共存留给未来独立域,见 ask_apps.sql 头注释)。

    语句顺序保证不撞 UNIQUE: 目标 region 行已存在则删除其他变体行;
    否则先把旧行 UPDATE 到目标 region,再 INSERT OR IGNORE 兜底。
    """
    own = conn is None
    conn = conn or connect()
    try:
        with conn:
            hit = conn.execute(
                "SELECT 1 FROM ask_apps WHERE app_code = ? AND region = ?",
                (app_code, region)).fetchone()
            if hit:
                conn.execute(
                    "DELETE FROM ask_apps WHERE app_code = ? AND region <> ?",
                    (app_code, region))
            else:
                conn.execute(
                    "UPDATE ask_apps SET region = ?,"
                    " updated_at = strftime('%Y-%m-%d %H:%M:%S','now','localtime')"
                    " WHERE app_code = ? AND region <> ?", (region, app_code, region))
                conn.execute(
                    "INSERT OR IGNORE INTO ask_apps (app_code, region) VALUES (?, ?)",
                    (app_code, region))
    finally:
        if own:
            conn.close()


def app_enabled(app_code: str) -> bool:
    """应用总开关;从未操作过的应用(无行)视为启用(惰性创建,不挡路)。"""
    state = app_state(app_code)
    return bool(state["enabled"]) if state else True


def set_app_state(app_code: str, *, enabled: bool | None = None,
                  app_name: str | None = None, app_desc: str | None = None,
                  region: str = "cn") -> dict[str, Any]:
    """更新主档字段(部分更新);行不存在先补齐,返回更新后的整行。

    ``app_name``/``app_desc`` 传 None 表示不改;要清空传空串(显式动作)。
    ``region`` 定位行(当前每应用一行,固定默认 cn;未来国际版是独立行)。
    """
    sets = ["updated_at = strftime('%Y-%m-%d %H:%M:%S','now','localtime')"]
    params: list[Any] = []
    if enabled is not None:
        sets.append("enabled = ?")
        params.append(1 if enabled else 0)
    if app_name is not None:
        sets.append("app_name = ?")
        params.append(app_name)
    if app_desc is not None:
        sets.append("app_desc = ?")
        params.append(app_desc)
    conn: sqlite3.Connection | None = None
    try:
        conn = connect()
        ensure_app(app_code, region, conn)
        with conn:
            # S608 豁免: 列名来自上面的固定字段白名单,无外部输入
            conn.execute("UPDATE ask_apps SET %s WHERE app_code = ? AND region = ?"  # noqa: S608
                         % ", ".join(sets), (*params, app_code, region))
        row = conn.execute(
            # S608 豁免: 列名是本模块固定白名单 _ROW_COLS,无外部输入
            "SELECT %s FROM ask_apps WHERE app_code = ? AND region = ?" % _ROW_COLS,  # noqa: S608
            (app_code, region)).fetchone()
    except sqlite3.Error as exc:
        raise AutoSignInKitError("应用主档更新失败: %s" % exc) from exc
    finally:
        if conn is not None:
            conn.close()
    return dict(row)
