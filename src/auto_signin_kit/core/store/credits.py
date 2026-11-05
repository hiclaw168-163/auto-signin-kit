"""积分与账户的业务查询(承接三个业务问题,全部现算不落派生数据):

- R1 每个应用每天获取多少积分   → :func:`credits_daily`(读 ``ask_daily_credits`` 视图)
- R2 有多少个账户在获取积分     → :func:`accounts_summary`(账户维表 × 近 N 天活跃)
- R3 所有账户每天总共可获取多少 → :func:`capacity_summary`(∑daily_potential vs 实际所得)

积分口径(与 ``runs.overview_stats`` 一致): 只认成功行(result ∈ RESULT_OK)
response JSON 里的 ``credits`` 字段,同一天多行累加;失败行不计。
``daily_potential`` 是**登记的**每日上限,按当前 enabled 账户现算——
它是"目标值"不是事实值,所以放账户维表由人维护,不从运行数据推断。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.store.connection import connect


def local_naive_now() -> str:
    """当前本地无时区时间(与台账 executed_at 同一口径)。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_account(domain: str, name: str = "default", *,
                   daily_potential: int | None = None) -> None:
    """登记账户(幂等);已存在时不覆盖任何字段。

    正常路径是落库自动建档(ingest);这个入口供 UI/CLI 显式登记还没跑过
    的账户(比如先设好每日上限,第一天就有 capacity 对比)。
    """
    conn = connect()
    try:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO ask_accounts (domain, name, daily_potential)"
                " VALUES (?, ?, ?)", (domain, name, daily_potential))
    finally:
        conn.close()


def set_account_potential(domain: str, name: str, daily_potential: int) -> None:
    """设定账户每日可获取积分上限;账户不存在则显式报错(不静默建档)。"""
    _update_account(domain, name, daily_potential=daily_potential)


def set_account_enabled(domain: str, name: str, enabled: bool) -> None:
    """账户启停(管理台开关,SPEC-12 P3);账户不存在则显式报错。

    注意口径: 这是**台账维表**的登记态(展示/容量计算用);调度执行的启停
    真相在各域 YAML 配置的 ``accounts[].enabled``(run_action 读配置)——
    两处要一起改,面板的开关只改登记态,配置由用户在配置页维护。
    """
    _update_account(domain, name, enabled=1 if enabled else 0)


#: 档案里要提取为**独立列**的字段(高频查询/业务关键维度,JSON 查询麻烦):
#: client_version ← 域方言 "version";install_path ← "install_path"/"data_dir"。
#: 提取后这些键不再进 client_info JSON;其余(机器级信息/域特有)留在 JSON。
_EXTRACTED_FIELDS = {
    "client_version": ("version", "client_version"),
    "device_id": ("device_id",),
    "install_path": ("install_path", "data_dir"),
    "uid": ("uid",),
}


def set_account_client_info(domain: str, name: str, info: dict[str, Any]) -> None:
    """写入账户的客户端档案;账户不存在则显式报错。

    关键字段(client_version/device_id/install_path/uid)提取为独立列,
    其余压缩进 client_info JSON;同键优先取靠前的方言形态。
    """
    info = dict(info)
    columns: dict[str, Any] = {}
    for column, keys in _EXTRACTED_FIELDS.items():
        for key in keys:
            if info.get(key) is not None:
                columns[column] = str(info[key])
                break
    for key in (k for keys in _EXTRACTED_FIELDS.values() for k in keys):
        info.pop(key, None)
    _update_account(domain, name,
                    client_info=json.dumps(info, ensure_ascii=False),
                    info_synced_at=local_naive_now(), **columns)


#: 允许更新的账户列(白名单,防止任意标识符进 SQL)。
_ACCOUNT_FIELDS = ("daily_potential", "enabled", "client_info", "info_synced_at",
                   "client_version", "device_id", "install_path", "uid")


def _update_account(domain: str, name: str, **fields: Any) -> None:
    """按 (domain, name) 更新账户列;没有命中行就显式报错。

    列名来自内部调用方的字面量 kwargs 并经白名单校验(无外部输入),
    值全部参数绑定——S608 豁免针对的是标识符拼接本身,表名/列名不可参数化。
    """
    if not fields or any(k not in _ACCOUNT_FIELDS for k in fields):
        raise AutoSignInKitError("非法的账户更新字段: %s" % sorted(fields))
    sets = ", ".join("%s = ?" % k for k in fields)
    conn = connect()
    try:
        with conn:
            updated = conn.execute(
                "UPDATE ask_accounts SET %s WHERE domain = ? AND name = ?" % sets,  # noqa: S608
                [*fields.values(), domain, name]).rowcount
        if not updated:
            raise AutoSignInKitError(
                "账户未登记: %s/%s(先 ensure_account 或跑一次签到)" % (domain, name))
    finally:
        conn.close()


def credits_daily(days: int = 14) -> list[dict[str, Any]]:
    """R1: 每应用每天获取的积分,近 N 天,新的在前;多账户时按日合计。"""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT day, domain, SUM(credits) AS credits FROM ask_daily_credits"
            " WHERE day >= date('now', 'localtime', ?)"
            " GROUP BY day, domain ORDER BY day DESC, domain",
            ["-%d days" % max(1, days)]).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def accounts_summary(days: int = 7) -> dict[str, Any]:
    """R2: 有多少账户在获取积分。

    ``total`` = 登记的账户数,``active`` = 近 N 天有积分所得的账户数;
    明细逐账户给出最近所得日期(没得过 = 还没跑出来,如实区分)。
    """
    conn = connect()
    try:
        registered = conn.execute(
            "SELECT domain, name, enabled, daily_potential, client_version,"
            " device_id, install_path, uid, client_info, info_synced_at"
            " FROM ask_accounts ORDER BY domain, name").fetchall()
        active_rows = conn.execute(
            "SELECT domain, account, MAX(day) AS last_day, SUM(credits) AS total"
            " FROM ask_daily_credits"
            " WHERE day >= date('now', 'localtime', ?)"
            " GROUP BY domain, account", ["-%d days" % max(1, days)]).fetchall()
    finally:
        conn.close()
    active = {(r["domain"], r["account"]): dict(r) for r in active_rows}
    items = []
    for r in registered:
        stat = active.get((r["domain"], r["name"]))
        client_info = None
        if r["client_info"]:
            try:
                client_info = json.loads(r["client_info"])
            except (TypeError, ValueError):
                client_info = None
        items.append({
            "domain": r["domain"], "name": r["name"],
            "enabled": bool(r["enabled"]),
            "daily_potential": r["daily_potential"],
            "active": stat is not None,
            "last_earned_day": stat["last_day"] if stat else None,
            "credits_recent": stat["total"] if stat else 0,
            "client_version": r["client_version"],
            "device_id": r["device_id"],
            "install_path": r["install_path"],
            "uid": r["uid"],
            "client_info": client_info,
            "info_synced_at": r["info_synced_at"],
        })
    return {"total": len(items), "active": sum(1 for i in items if i["active"]),
            "window_days": max(1, days), "accounts": items}


def capacity_summary(days: int = 7) -> list[dict[str, Any]]:
    """R3: 所有账户每天总共可获取多少积分 vs 实际所得。

    ``potential`` = 当前 enabled 账户的 daily_potential 合计(有账户未设定
    时该天 potential 为 NULL——不猜数);``earned`` = 当天实际所得;
    ``rate`` = 所得/上限(上限未设定时不给百分比,不给假精度)。
    """
    conn = connect()
    try:
        potential_row = conn.execute(
            "SELECT SUM(daily_potential) AS p FROM ask_accounts"
            " WHERE enabled = 1 AND daily_potential IS NOT NULL").fetchone()
        earned_rows = conn.execute(
            "SELECT day, SUM(credits) AS earned FROM ask_daily_credits"
            " WHERE day >= date('now', 'localtime', ?)"
            " GROUP BY day ORDER BY day DESC", ["-%d days" % max(1, days)]
        ).fetchall()
    finally:
        conn.close()
    potential = potential_row["p"]  # 全部未设定时为 NULL,如实透出
    out = []
    for row in earned_rows:
        earned = row["earned"]
        rate = round(earned / potential, 4) if potential else None
        out.append({"day": row["day"], "earned": earned,
                    "potential": potential, "rate": rate})
    return out
