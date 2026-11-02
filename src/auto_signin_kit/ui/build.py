"""前端构建编排: 把 npm install/build 收进门禁内(SPEC-09)。

为什么是 CLI 命令而不是启动脚本里的 if 分支: STD-06 红线——脚本一有业务
分支就必须改写成 CLI;''dist 缺不缺、npm 在不在、构建失败怎么报''这些判断
要可测试、报错可读,所以住进 Python 层,启动脚本只做无分支的纯调用。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from auto_signin_kit.core.errors import ConfigError

#: editable 安装时 src/auto_signin_kit/ui/build.py 的上三级就是仓库根。
FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"

#: npm install + build 的总超时;首次拉依赖可能较慢。
BUILD_TIMEOUT = 900


def dist_dir() -> Path:
    """前端构建产物目录(frontend/dist,不入库)。"""
    return FRONTEND_DIR / "dist"


def is_built() -> bool:
    """产物是否就绪(以 index.html 存在为准,空 dist 目录不算)。"""
    return (dist_dir() / "index.html").is_file()


def _npm() -> str:
    npm = shutil.which("npm")
    if npm is None:
        raise ConfigError(
            "未找到 npm(Node.js)。请先安装 Node.js 18+ 再重试;"
            "只用 CLI/定时任务的话不需要构建前端。")
    return npm


def build() -> Path:
    """npm install + npm run build;失败显式报错(带 npm 输出尾巴),不静默。"""
    npm = _npm()
    for args in (["install"], ["run", "build"]):
        try:
            proc = subprocess.run(  # noqa: S603 — 固定参数,无外部拼接
                [npm, *args],
                cwd=FRONTEND_DIR,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=BUILD_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConfigError(
                "npm %s 超过 %ds 被终止" % (" ".join(args), BUILD_TIMEOUT)) from exc
        if proc.returncode != 0:
            tail = ((proc.stderr or "") + (proc.stdout or ""))[-2000:]
            raise ConfigError(
                "npm %s 失败(exit %d):\n%s" % (" ".join(args), proc.returncode, tail))
    if not is_built():
        raise ConfigError("npm build 退出码为 0 但 dist/index.html 不存在,构建产物异常")
    return dist_dir()


def ensure_built(if_missing: bool) -> bool:
    """确保产物就绪;``if_missing=True`` 时已构建则跳过。返回是否执行了构建。

    ``if_missing=False`` 且未构建 → 显式报错(不做静默兜底,AGENTS.md 规则 4)。
    """
    if is_built():
        return False
    if not if_missing:
        raise ConfigError(
            "前端尚未构建。执行 auto-signin-kit ui build(需要 Node.js 18+),"
            "或启动时用 scripts/启动管理台 自动补建。")
    build()
    return True
