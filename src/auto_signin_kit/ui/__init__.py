"""Web 管理台的 Python 侧: FastAPI API 层(SPEC-09)。

本包只做**编排**,零业务逻辑:

- 读数据: 进程内调 core.store(SQLite 台账)与 schedule 域 service(纯查询)
- 触发: 经 :mod:`auto_signin_kit.ui.runner` 子进程调 ``auto-signin-kit`` CLI,
  复用其退出码与 JSON 输出契约——ui 层不 import 任何域的 cli 模块
  (import-linter 契约 ``ui 不碰各域 cli`` 拦着)
"""
