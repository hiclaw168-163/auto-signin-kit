"""WorkBuddy 凭据文件定位与读取(带并发锁重试)。

凭据文件由桌面端登录后自动写出;客户端刷新 token 时会短暂独占文件,
恰好在那一刻读取会撞 PermissionError——锁是瞬时的,等两秒再试即可。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

_URL_STATIC = Path("CodeBuddyExtension") / "Data" / "Public" / "auth"
#: Windows/macOS 桌面端凭据相对路径(分别拼在 %LOCALAPPDATA% 与 Application Support 下)。
AUTH_BASENAME = _URL_STATIC / "workbuddy-desktop.info"
# Linux 没有桌面端,入口是 CodeBuddy CLI;它写出的凭据文件名不同、放在 XDG 数据目录
# (实测: ~/.local/share/.../Tencent-Cloud.coding-copilot.info,JSON 结构与桌面端一致)。
CLI_AUTH_BASENAME = _URL_STATIC / "Tencent-Cloud.coding-copilot.info"


def _candidate_paths() -> list[Path]:
    home = Path.home()
    app_data_local = os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local"
    xdg_data = os.environ.get("XDG_DATA_HOME") or home / ".local" / "share"
    return [
        Path(app_data_local) / AUTH_BASENAME,                       # Windows 桌面端
        home / "Library" / "Application Support" / AUTH_BASENAME,   # macOS 桌面端
        Path(xdg_data) / CLI_AUTH_BASENAME,                         # Linux CodeBuddy CLI
        home / ".config" / AUTH_BASENAME,                           # Linux(旧猜测,保留)
        home / ".workbuddy" / "auth" / "workbuddy-desktop.info",    # 兜底
    ]


def find_auth_file() -> tuple[Path | None, list[str]]:
    """按平台探测凭据文件;返回 (路径或 None, 实际检查过的候选列表)。

    环境变量 ``WORKBUDDY_AUTH_FILE`` 优先;不存在时单独报告该路径,不给无关建议。
    """
    override = os.environ.get("WORKBUDDY_AUTH_FILE")
    if override:
        path = Path(override)
        candidates = [str(path)]
        return (path if path.exists() else None), candidates
    candidates = [str(c) for c in _candidate_paths()]
    for text in candidates:
        if Path(text).exists():
            return Path(text), candidates
    return None, candidates


def load_session(auth_file: Path) -> dict:
    """读取凭据 JSON。"""
    return json.loads(auth_file.read_text(encoding="utf-8"))


def load_session_retry(auth_file: Path, attempts: int = 3, delay: float = 2.0) -> dict:
    """带重试的凭据读取;锁是瞬时的,撞 PermissionError 稍候再读。"""
    last: Exception | None = None
    for index in range(attempts):
        try:
            return load_session(auth_file)
        except PermissionError as exc:
            last = exc
            if index < attempts - 1:
                time.sleep(delay)
    assert last is not None
    raise last
