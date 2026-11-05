"""台账 schema 与存量迁移(从 store.py 拆出,保持单文件行数合规)。

职责: 表/索引/视图 DDL、老库补列改名、存量行规整(时区抹平/结果词收敛/
request-response 回填)。store.py 只管读写,这里只管"库长什么样、怎么升级"。

**DDL 唯一来源是仓库根的 ``data/sql/`` 目录**(按表分文件 + indexes.sql +
views.sql;目录本身被 data/ 的 gitignore 规则排除,但 sql/ 例外进版本库),
本模块只负责装载与按序执行——建表语句不在 Python 里写第二份,两边漂移是
红线。装载锚点是 :func:`project_root`(editable 安装时即仓库根,与 logs/、
data/ 的定位同机制)。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from auto_signin_kit.core.adapters.log_dir import project_root
from auto_signin_kit.core.errors import AutoSignInKitError

#: sql/ 目录里建表文件的装载顺序(与旧 _SCHEMA 的执行顺序一致)。
_TABLE_SQL_FILES = ("ask_signin_runs.sql", "ask_schedule_changes.sql", "ask_apps.sql",
                    "ask_accounts.sql")


def _load_sql(filename: str) -> str:
    """从仓库根 ``data/sql/`` 读一份 DDL;定位失败显式报错(不做静默兜底)。"""
    root = project_root()
    if root is None:
        raise AutoSignInKitError(
            "data/sql/ 目录定位失败: 非 editable 安装下 project_root 不可得,"
            "请用 uv tool install --editable 安装")
    path = root / "data" / "sql" / filename
    if not path.is_file():
        raise AutoSignInKitError("DDL 文件缺失: %s" % path)
    return path.read_text(encoding="utf-8")


#: ask_accounts 建表 DDL 单独引用: 建表(_SCHEMA)与列序重建(_migrate)共用,
#: 避免 DDL 双份漂移。
_ASK_ACCOUNTS_DDL = _load_sql("ask_accounts.sql")

#: 全部建表 DDL(新库建表用)。
_SCHEMA = "\n".join(_load_sql(f) for f in _TABLE_SQL_FILES)

#: 依赖新列/新列名的索引与视图,必须在 _migrate 补列改名**之后**执行,
#: 不能进上面的建表脚本。
_SCHEMA_AFTER_MIGRATE = (_load_sql("indexes.sql") + "\n"
                         + _load_sql("views.sql"))

#: ask_accounts 的目标列序(列序习惯: 主键 → 主体与开关 → 身份标识 → 积分
#: 业务 → 客户端档案 → 统一审计字段;历史 ALTER 追加会打乱重点列位置)。
_ACCOUNT_COLUMN_ORDER = ("id", "domain", "name", "enabled", "uid", "device_id",
                         "client_version", "daily_potential", "install_path",
                         "client_info", "info_synced_at", "created_at",
                         "updated_at")

#: ask_signin_runs 的目标列序(主键 → 业务主体 → 时间 → 动作状态 → 明细 → 审计)。
_RUNS_COLUMN_ORDER = ("id", "domain", "account_name", "executed_at", "operation",
                      "source", "result", "needs_attention", "report",
                      "request", "response", "detail", "created_at",
                      "updated_at")

#: ask_apps 的目标列序(主键 → 业务主体 → 开关 → 展示描述 → 审计)。
_APP_COLUMN_ORDER = ("id", "app_code", "region", "enabled", "app_name",
                     "app_desc", "created_at", "updated_at")

#: 列名规约: 避开数据库关键字——SQLite 官方关键字表里有 ACTION,
#: MySQL 官方关键字表里有 ACTION/ACCOUNT;ask_apps 业务键统一 app_ 前缀
#: (domain/display/note 是 2026-11-05 首版命名,重设计为 app_code 等)。
_RENAME_MIGRATIONS = (
    ("ask_signin_runs", "action", "operation"),
    ("ask_schedule_changes", "action", "operation"),
    ("ask_signin_runs", "account", "account_name"),
    ("ask_apps", "domain", "app_code"),
    ("ask_apps", "display", "app_name"),
    ("ask_apps", "note", "app_desc"),
)

#: 老库迁移: ALTER 加列后,把历史上靠 action 标记的回填行规整到 source 列;
#: 再补 request/response 两列(请求参数与响应结果配对落库);
#: created_at/updated_at 是全表统一审计字段,回填 executed_at(业务时序)。
_MIGRATIONS = (
    ("source", "ALTER TABLE ask_signin_runs ADD COLUMN source TEXT NOT NULL DEFAULT 'run'",
     "UPDATE ask_signin_runs SET source = 'backfill' WHERE operation = 'backfill'"),
    ("request", "ALTER TABLE ask_signin_runs ADD COLUMN request TEXT",
     None),
    ("response", "ALTER TABLE ask_signin_runs ADD COLUMN response TEXT",
     None),
    # account_name: 本次运行用的账户(当前每域一户 'default',一域多户时由域回传);
    # 存量行统一回填 default(视图按账户分组,不能留 NULL)。历史列名 account
    # 是 MySQL 关键字,改名走上面的 _RENAME_MIGRATIONS,这里只补"从没有过"的库
    ("account_name", "ALTER TABLE ask_signin_runs ADD COLUMN account_name TEXT",
     "UPDATE ask_signin_runs SET account_name = 'default'"
     " WHERE account_name IS NULL"),
    ("created_at", "ALTER TABLE ask_signin_runs ADD COLUMN created_at TEXT",
     "UPDATE ask_signin_runs SET created_at = executed_at"
     " WHERE created_at IS NULL"),
    ("updated_at", "ALTER TABLE ask_signin_runs ADD COLUMN updated_at TEXT",
     "UPDATE ask_signin_runs SET updated_at = executed_at"
     " WHERE updated_at IS NULL"),
)

#: 存量行规整(幂等,每次 connect 跑): 旧版 executed_at 带 +08:00 时区后缀,
#: 裸 date() 会转 UTC;统一抹成本地无时区。结果词表 19 → 10 的历史值一并收敛。
_ROW_NORMALIZE_SQL = (
    "UPDATE ask_signin_runs SET executed_at = strftime('%Y-%m-%d %H:%M:%S',"
    " executed_at, 'localtime') WHERE executed_at LIKE '%+%';",
    "UPDATE ask_schedule_changes SET executed_at = strftime('%Y-%m-%d %H:%M:%S',"
    " executed_at, 'localtime') WHERE executed_at LIKE '%+%';",
    # 认证类 → NO_AUTH
    "UPDATE ask_signin_runs SET result='NO_AUTH'"
    " WHERE result IN ('NO_SESSION','AUTH_ERROR','AUTH_REJECTED','FORBIDDEN');",
    # 失败类收敛(未激活/限流/通用 ERROR)
    "UPDATE ask_signin_runs SET result='FAILED'"
    " WHERE result IN ('ERROR','FAIL','FAILURE','INACTIVE','RATE_LIMITED','LIMITED');",
    # 网络类收敛(超时是网络问题的一种)
    "UPDATE ask_signin_runs SET result='NETWORK'"
    " WHERE result IN ('TIMEOUT','TIMEOUT_ERROR','NETWORK_ERROR','NET_ERROR');",
    # 待生效 → OK;活动不在期 → 今日未活跃
    "UPDATE ask_signin_runs SET result='OK' WHERE result = 'PENDING';",
    "UPDATE ask_signin_runs SET result='NO_ACTIVITY' WHERE result = 'INACTIVE_ERROR';",
)

#: 事件信封字段——进独立列或表结构语义,不进 request/response。
ENVELOPE_FIELDS = frozenset({
    "ts", "level", "domain", "action", "source", "result",
    "needs_attention", "report", "request", "detail", "account",
})


def local_naive(ts: str) -> str:
    """ISO 时间戳 → 本地无时区 'YYYY-MM-DD HH:MM:SS'。

    时区不逐行落库: 全库统一机器系统时区(统一配置),带时区后缀只会让
    date() 查询在 UTC/本地之间反复踩坑(2026-11-03 凌晨调度"被消失"的根因)。
    """
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return str(ts)
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _split_record(record: dict) -> tuple[str | None, str | None]:
    """整行事件 → (request JSON, response JSON);信封字段留独立列。"""
    request = record.get("request") or {}
    response = {k: v for k, v in record.items() if k not in ENVELOPE_FIELDS}
    return (
        json.dumps(request, ensure_ascii=False) if request else None,
        json.dumps(response, ensure_ascii=False) if response else None,
    )


def _reorder_table(conn: sqlite3.Connection, table: str,
                   target_order: tuple[str, ...], ddl: str) -> None:
    """表实际列序与目标不一致(历史 ALTER 追加/目标重排导致)时整表重建搬数据;
    一致则跳过(幂等)。

    SQLite 不支持调整列序,只能改名→按目标 DDL 重建→按名列拷贝→删旧。
    AUTOINCREMENT 的 sqlite_sequence 随显式 id 插入与 RENAME 自动跟随,
    重建不丢自增计数;UNIQUE 约束由目标 DDL 原样带上。前置条件: 目标 DDL
    的 NOT NULL 列都不能有 NULL(调用方先补列回填,见 :func:`_migrate`)。
    """
    actual = tuple(
        row["name"] for row in conn.execute("PRAGMA table_info(%s)" % table))
    if actual == target_order:
        return
    column_list = ", ".join(target_order)
    with conn:  # 整表重建单事务: 中断则旧表还在,下次连接重来
        conn.execute("ALTER TABLE %s RENAME TO %s_old" % (table, table))
        conn.execute(ddl)  # 旧表已改名,IF NOT EXISTS 直接建新
        # S608 豁免: 表/列名来自模块级目标序常量,无外部输入(列清单不可参数化)
        conn.execute(
            "INSERT INTO %s (%s) SELECT %s FROM %s_old"  # noqa: S608
            % (table, column_list, column_list, table))
        conn.execute("DROP TABLE %s_old" % table)


def _migrate(conn: sqlite3.Connection) -> None:
    """老库补列/改名 + 存量行规整(全部幂等): CREATE TABLE IF NOT EXISTS 不会动已有表。"""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(ask_signin_runs)")}
    # 列名规约: action 是 SQLite 关键字,统一改名 operation(RENAME 自动更新
    # 引用该列的索引/触发器定义;两表都是先改名,后面补列重建按新名)
    for table, old, new in _RENAME_MIGRATIONS:
        tcols = {row["name"] for row in conn.execute("PRAGMA table_info(%s)" % table)}
        if old in tcols and new not in tcols:
            with conn:
                conn.execute("ALTER TABLE %s RENAME COLUMN %s TO %s"
                             % (table, old, new))
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(ask_signin_runs)")}
    # ts → executed_at: 字段名见名知义(值不变,仍是 ISO8601 时间戳);
    # RENAME COLUMN 会自动更新引用该列的索引定义
    if "ts" in columns and "executed_at" not in columns:
        with conn:
            conn.execute("ALTER TABLE ask_signin_runs RENAME COLUMN ts TO executed_at")
    sched_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(ask_schedule_changes)")}
    if "ts" in sched_columns and "executed_at" not in sched_columns:
        with conn:
            conn.execute("ALTER TABLE ask_schedule_changes RENAME COLUMN ts TO executed_at")
    # ask_schedule_changes 统一审计字段: 审计行只插不改,回填 executed_at
    # (S608 豁免: 列名来自模块级字面量元组,无外部输入,列名不可参数化)
    for col in ("created_at", "updated_at"):
        if col not in sched_columns:
            with conn:
                conn.execute("ALTER TABLE ask_schedule_changes ADD COLUMN %s TEXT" % col)
                conn.execute(
                    "UPDATE ask_schedule_changes SET %s = executed_at" % col)  # noqa: S608
    # ask_accounts 档案列: 早期建表(2026-11-03 前)没有这些列,逐列补上(幂等);
    # _SCHEMA 先行执行,表到这里必然已存在
    acct_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ask_accounts)")}
    for col in ("client_info", "info_synced_at", "client_version", "device_id",
                "install_path", "uid", "created_at", "updated_at"):
        if col not in acct_columns:
            with conn:
                conn.execute("ALTER TABLE ask_accounts ADD COLUMN %s TEXT" % col)
    # 统一审计字段回填(必须在 _reorder 之前: 目标 DDL 对这两列是 NOT NULL)
    conn.execute("UPDATE ask_accounts SET created_at = strftime("
                 "'%Y-%m-%d %H:%M:%S','now','localtime') WHERE created_at IS NULL")
    conn.execute("UPDATE ask_accounts SET updated_at = strftime("
                 "'%Y-%m-%d %H:%M:%S','now','localtime') WHERE updated_at IS NULL")
    # signin_runs 补列(含统一审计字段),补上的列立即做一次性回填
    # (只在新补时跑一次: source 的回填有语义,反复跑会翻转既有行)
    for name, add_sql, backfill_sql in _MIGRATIONS:
        if name not in columns:
            with conn:
                conn.execute(add_sql)
                if backfill_sql:
                    conn.execute(backfill_sql)
    # ask_apps.region: 应用主档的版本区(国内版/国际版);首版建表没有此列,
    # 老库补列时存量行回填 'cn'(当前每应用都是国内版)
    apps_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ask_apps)")}
    if apps_columns and "region" not in apps_columns:
        with conn:
            conn.execute("ALTER TABLE ask_apps ADD COLUMN region"
                         " TEXT NOT NULL DEFAULT 'cn'")
    # 列序重建(必须在补列之后: 按名列拷贝要求旧表已有全部目标列)
    _reorder_table(conn, "ask_apps", _APP_COLUMN_ORDER, _load_sql("ask_apps.sql"))
    _reorder_table(conn, "ask_accounts", _ACCOUNT_COLUMN_ORDER, _ASK_ACCOUNTS_DDL)
    _reorder_table(conn, "ask_signin_runs", _RUNS_COLUMN_ORDER,
                   _load_sql("ask_signin_runs.sql"))
    # 存量行规整(幂等): 时区抹平 + 旧结果词收敛;放在视图重建之前,
    # 视图的 date() 才能按"本地裸时间"正确取日期
    for sql in _ROW_NORMALIZE_SQL:
        conn.execute(sql)
    # 老行回填 request/response(从 detail JSON 拆列;幂等,只补空行)
    stale = conn.execute(
        "SELECT id, detail FROM ask_signin_runs"
        " WHERE response IS NULL AND detail IS NOT NULL").fetchall()
    updates = []
    for row in stale:
        try:
            record = json.loads(row["detail"])
        except (TypeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        request, response = _split_record(record)
        updates.append((request, response, row["id"]))
    if updates:
        with conn:
            conn.executemany(
                "UPDATE ask_signin_runs SET request = ?, response = ? WHERE id = ?",
                updates)
