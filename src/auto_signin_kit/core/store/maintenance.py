"""台账维护: 自增 id 重排等跨表整理操作(与日常读查分离,偶尔手动触发)。"""

from __future__ import annotations

from typing import Any

from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.store.connection import connect

#: 参与自增 id 重排的表(清单化,SQL 用字面量拼,不做动态表名拼接)。
_RESQUENCE_TABLES = ("ask_signin_runs", "ask_schedule_changes", "ask_accounts")


def resequence_ids() -> dict[str, int]:
    """台账各表的自增 id 从 1 连续重排(按 ``executed_at, id`` 顺序)。

    背景: 去重/清理(compact_history 等)删行后 id 会出现空洞。id 只在库内
    自洽(视图按行自关联),没有外部引用,重排安全;AUTOINCREMENT 的
    ``sqlite_sequence`` 不会自动回落,重排后显式指到最大 id,后续插入从
    max+1 连续接着走。每张表整表单事务重插,重排后行数对不上就抛错如实报告。
    """
    result: dict[str, int] = {}
    conn = connect()
    try:
        for table in _RESQUENCE_TABLES:
            # S608 逐处豁免: 表名来自模块级白名单 _RESQUENCE_TABLES,
            # 列名来自游标 description,均无外部输入,拼接是必要的(表名不可参数化)
            columns = [r["name"] for r in conn.execute(
                "PRAGMA table_info(%s)" % table).fetchall()]
            # 排序键: 有时间列的按 (executed_at, id) 保持业务时序,纯维表按 id
            order = "executed_at, id" if "executed_at" in columns else "id"
            rows = conn.execute(
                "SELECT * FROM %s ORDER BY %s" % (table, order)).fetchall()  # noqa: S608
            if not rows:
                # 空表也要把 sequence 计数清掉,否则 AUTOINCREMENT 记着旧值,
                # 下一条插入不会从 1 开始
                with conn:
                    conn.execute(
                        "DELETE FROM sqlite_sequence WHERE name = ?", (table,))
                result[table] = 0
                continue
            columns = list(rows[0].keys())
            placeholders = ", ".join("?" * len(columns))
            with conn:  # 整表删+重插在一个事务里,中断则整体回滚
                conn.execute("DELETE FROM %s" % table)  # noqa: S608
                for new_id, row in enumerate(rows, start=1):
                    values = [new_id if c == "id" else row[c] for c in columns]
                    conn.execute(
                        "INSERT INTO %s (%s) VALUES (%s)"  # noqa: S608
                        % (table, ", ".join(columns), placeholders), values)
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM %s" % table).fetchone()["n"]  # noqa: S608
            if count != len(rows):
                raise AutoSignInKitError(
                    "id 重排校验失败: %s 重排前 %d 行,重排后 %d 行"
                    % (table, len(rows), count))
            _point_sequence_to_max(conn, table, len(rows))
            result[table] = count
    finally:
        conn.close()
    return result


def _point_sequence_to_max(conn: Any, table: str, max_id: int) -> None:
    """把 ``sqlite_sequence`` 显式指到重排后的最大 id。

    AUTOINCREMENT 的单调计数器不会因显式插入小 id 自动回落(它的语义
    是"永不复用"),不手动指位的话后续插入仍从旧值继续。
    """
    with conn:
        updated = conn.execute(
            "UPDATE sqlite_sequence SET seq = ? WHERE name = ?",
            (max_id, table)).rowcount
        if not updated:
            conn.execute(
                "INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)",
                (table, max_id))
