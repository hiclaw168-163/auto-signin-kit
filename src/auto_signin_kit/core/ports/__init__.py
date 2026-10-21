"""端口层: 只放形状(Protocol / 数据对象),不依赖 adapters 的具体实现。

按 `AGENTS.md` 硬性规则 5,**只在真的会出现第二种实现时才建端口**。
已建的端口:

- ``scheduler``: 定时调度(Windows 任务计划程序 / macOS launchd / Linux cron),
  三种实现真实存在,不是提前设计。

没有第二种实现的 OS 差异直接进 ``core/adapters/``,不在这里加一层。
"""
