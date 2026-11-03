"""一键初始化: 环境自检 → 选域 → 就绪探测 → 注册调度 → 拉起管理台。

这是顶层内置命令(与 ui 同级),不属于任何业务域;经
``auto_signin_kit.builtins`` entry-point 组挂载到根 CLI。
"""

from auto_signin_kit.init.cli import app

__all__ = ["app"]
