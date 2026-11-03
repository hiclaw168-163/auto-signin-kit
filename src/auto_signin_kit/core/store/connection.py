"""台账连接层: 库文件定位与连接建立(全部表操作的共同入口)。

并发: 多个定时任务可能同时写,连接开 WAL + 3s busy_timeout;单行短事务,
够用且无需引入任何外部依赖(标准库 sqlite3)。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from auto_signin_kit.core.adapters.log_dir import default_data_dir
from auto_signin_kit.core.store_migrations import (
    _SCHEMA,
    _SCHEMA_AFTER_MIGRATE,
    _migrate,
)

#: 台账文件名(运行时数据目录,与日志分居;同样被 .gitignore 排除)。
LEDGER_FILENAME = "auto-signin-kit.db"


def db_path() -> Any:
    """台账文件路径: 运行时数据目录(尊重 ``AUTO_SIGNIN_KIT_DATA_DIR``)。"""
    return default_data_dir() / LEDGER_FILENAME


def connect() -> sqlite3.Connection:
    """打开台账连接并确保表结构存在(含老库加列迁移);调用方负责 close。

    顺序有讲究: 建表 → 老库补列/存量规整 → 建依赖新列的索引/视图,
    否则老库上视图/索引引用尚不存在的列会直接报错。
    """
    conn = sqlite3.connect(db_path(), timeout=3.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    # 视图重建(DROP+CREATE)必须原子: 多进程并发 connect(CLI 落库 vs UI 服务)时,
    # 两个非事务的 executescript 交错会在 CREATE VIEW 撞 "already exists"。
    # BEGIN IMMEDIATE 拿写锁把整个脚本串行化,抢不到锁的等 busy_timeout。
    conn.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA_AFTER_MIGRATE + "\nCOMMIT;")
    return conn
