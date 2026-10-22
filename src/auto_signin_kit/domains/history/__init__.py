"""history 域: 签到台账查询与历史日志回填。

数据源是 ``core/store.py`` 的 SQLite 台账(签到结果与调度变更各一张实体表),
写入端在 ``core/report.py`` 的 emit_result——本域只读不写(除 import 回填)。
"""

from auto_signin_kit.domains.history.cli import app

__all__ = ["app"]
