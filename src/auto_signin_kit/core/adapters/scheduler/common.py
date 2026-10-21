"""定时调度适配器的公共部分: 可执行文件定位、时间解析、XML 转义。

只放三个平台都用得上、且与 OS 无关的东西;平台专属命令(schtasks /
launchctl / crontab)只出现在 windows.py / darwin.py / linux.py 各自内部。
"""

from __future__ import annotations

import locale
import re
import shutil
from pathlib import Path

from auto_signin_kit.core.errors import AdapterError

#: ``HH:MM``;允许 ``9:05`` 这种单位数小时写法,输出时统一补零。
_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


class SchedulerError(AdapterError):
    """定时调度失败。

    ``reason`` 是稳定的错误类别(``EXE_NOT_FOUND`` / ``BAD_TIME`` /
    ``INSTALL_FAILED`` / ``UNINSTALL_FAILED`` / ``UNSUPPORTED``),由域层
    转译成给用户看的一句话;message 只补充必要上下文,**不带平台原始输出**。
    """

    def __init__(self, reason: str, message: str = "") -> None:
        """以 reason 为主体,message 作为补充,避免平台细节泄进异常文本。"""
        super().__init__(reason if not message else f"{reason}: {message}")
        self.reason = reason


def resolve_executable(argv0: str) -> Path:
    """把命令名解析成绝对路径;找不到直接报错,不做静默兜底。

    定时任务由系统进程拉起,没有交互式 shell 的 PATH,所以这里必须是绝对路径——
    相对命令名在任务里会静默失败(任务显示"已运行"但什么都没发生)。
    """
    found = shutil.which(argv0)
    if not found:
        raise SchedulerError(
            "EXE_NOT_FOUND",
            f"在 PATH 里找不到 {argv0!r};请先执行 "
            "uv tool install --editable . --force,或把其所在目录加入 PATH",
        )
    return Path(found)


def exe_text(argv0: str) -> str:
    """命令名 → 绝对路径字符串;找不到就原样返回。

    只给 ``render``(dry-run 预览)用: 预览的目的是让人看到"将要写什么",
    即使本机还没装也该出得来,不该被硬失败打断。真正注册走
    :func:`resolve_executable`,那里必须找到。
    """
    return shutil.which(argv0) or argv0


def parse_hhmm(value: str) -> tuple[int, int]:
    """解析 ``HH:MM`` 为 ``(时, 分)``;格式错或越界抛 ``SchedulerError``。"""
    match = _HHMM_RE.match(value.strip())
    if not match:
        raise SchedulerError("BAD_TIME", f"时间格式应为 HH:MM,收到 {value!r}")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise SchedulerError("BAD_TIME", f"时间越界: {value!r}")
    return hour, minute


def xml_escape(text: str) -> str:
    """XML 文本转义;任务名与路径里出现 ``&`` ``<`` ``>`` ``"`` 会破坏 XML。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _decode(raw: bytes) -> str:
    """按系统编码解码子进程输出。

    Windows 的 schtasks 输出的是控制台 ANSI 编码(中文系统即 GBK),直接按
    UTF-8 解会得到一坨问号——那样连"用户名或密码不正确"这种关键提示都读不出来,
    排查方向会被带偏。所以先试 UTF-8,出现替换字符就退回系统编码。
    """
    for encoding in ("utf-8", locale.getpreferredencoding(False), "mbcs"):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if "�" not in text:
            return text
    return raw.decode("utf-8", "replace")


def brief_output(out: bytes, err: bytes) -> str:
    """从子进程输出里取一行人类可读摘要;拿不到就返回空串。"""
    raw = _decode(err or out or b"").strip()
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def hhmm(hour: int, minute: int) -> str:
    """``(时, 分)`` → ``HH:MM``,补零。"""
    return f"{hour:02d}:{minute:02d}"
