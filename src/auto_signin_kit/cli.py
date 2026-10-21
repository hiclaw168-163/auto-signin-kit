"""auto_signin_kit CLI 入口(组合根): 经 entry_points 挂载业务域。

这是整个架构里"域注册"这条决策唯一落地的地方——新增一个域只改
pyproject.toml 加一行 entry point,这个文件永远不用碰。如果你发现自己
正在这个文件里 import 某个具体域,说明哪里错了。
"""

import sys
from importlib.metadata import entry_points

import typer

from auto_signin_kit.core.errors import AutoSignInKitError
from auto_signin_kit.core.logging import log_error


def build_app() -> typer.Typer:
    """遍历 auto_signin_kit.domains entry_points 挂载各域。"""
    root = typer.Typer(help="auto-signin-kit: 各编程助手每日签到/领奖的自动化工具箱")
    eps = sorted(entry_points(group="auto_signin_kit.domains"), key=lambda e: e.name)
    if not eps:
        raise RuntimeError(
            "未发现 auto_signin_kit.domains entry_points。"
            "请在仓库根执行: uv sync && uv tool install --editable . --force"
            # entry_points 写在包的 dist-info 里,只改 pyproject.toml 不会自动刷新,
            # 这是一个真实会踩到、但报错信息一给就能自己解决的坑。
        )
    for ep in eps:
        root.add_typer(ep.load(), name=ep.name)
    return root


def main() -> None:
    """CLI 入口: 业务异常转对应退出码,entry_points 缺失给友好提示。"""
    # 架构规则要求 OS 差异进 core/adapters/,这里是唯一例外: Windows 控制台
    # 默认编码会导致中文输出乱码,在组合根统一 reconfigure 比每个域各自处理更务实。
    if sys.platform == "win32":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    try:
        app = build_app()
        app()
    except AutoSignInKitError as e:
        log_error(str(e))
        sys.exit(e.exit_code)
    except RuntimeError as e:
        log_error(f"启动失败: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
