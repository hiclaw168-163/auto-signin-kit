"""workbuddy 域业务逻辑。

**这个文件禁止 ``import typer`` 或 ``from typer import ...``。**
不是风格偏好,是有门禁测试真的会拦下来的硬规则(见
``tests/test_architecture_gate.py`` 的 ``test_service_modules_do_not_import_typer``)。

原因: 这层要能被单测直接调用、被将来的第二入口(TUI/Web/其他脚本)复用,
不能跟"参数怎么从命令行解析"这件事绑死。如果需要用户交互确认,
用 ``auto_signin_kit.core.prompt`` 里的 ``prompt_confirm``/``prompt_str``,
不要引入 typer。

**这个文件也不该直接调用另一个域的内部函数。** 域与域之间要保持独立
(见 ``pyproject.toml`` 里的 ``domains 之间彼此独立`` 契约)——如果确实
需要跨域编排(比如"提交代码后触发部署"这种业务上真实存在的组合),
把它写成显式的例外并记录进 import-linter 契约的 ``ignore_imports``,
不要让耦合悄悄蔓延。
"""

from __future__ import annotations

from auto_signin_kit.core.errors import CommandError  # noqa: F401  # 按需使用


def do_something(arg: str) -> str:
    """替换成真正的业务逻辑;这只是一个占位示例,证明三件套的调用链路是通的。"""
    if not arg:
        raise CommandError("arg 不能为空")
    return f"workbuddy 处理了: {arg}"
