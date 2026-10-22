"""全局测试隔离: 任何测试都不许碰真实的日志、数据目录与 SQLite 台账。

各域的 emit_result 会向日志目录写文件、向台账(core/store.py)落库;不隔离的话,
跑一次测试套件就会把 NO_AUTH 之类的假记录写进项目 logs/ 的真实日志与
``signin_ledger.db``(2026-10-22 实际发生过: 台账里混进了 pytest 临时目录的
NO_AUTH 行)。这里统一把日志/数据目录环境变量指到每个测试自己的临时目录——
日志文件与台账路径都从它们解析,一处隔离两边生效。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_log_and_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_SIGNIN_KIT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("AUTO_SIGNIN_KIT_DATA_DIR", str(tmp_path / "data"))
