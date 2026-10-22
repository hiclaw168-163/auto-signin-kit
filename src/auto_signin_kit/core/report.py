"""签到结果输出: JSON Lines 事件契约 + 令牌脱敏 + 静默日志双通道。

对应签到脚本长期沿用的 ``emit`` 契约,输出升级为 JSON Lines 事件格式
(每行一个完整 JSON 对象,stdout 与日志文件同一条记录):

- 结果判定用 action 前缀: ``silent`` 写日志文件(pythonw 下 stdout 是 None,
  print 静默成功等于扔进黑洞,NO_AUTH 这类最需要人处理的结果会消失)。
- ``ERROR`` 结果两条通道都走: 即便计划任务把参数写错(silent 拼错之类),
  这次失败也有痕迹。
- 自定义日志路径不可写时回退脚本目录/cwd,避免无窗口运行下结果彻底丢失。
- 所有已知敏感值(token)在序列化后统一替换为 ``[REDACTED]``。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_signin_kit.core.logging import log_debug

#: 出现这些 result 时,``needs_attention`` 默认置 True(需人工介入)。
ATTENTION_RESULTS = frozenset({
    "ERROR", "UNKNOWN", "NETWORK", "TIMEOUT", "NO_AUTH", "NO_SESSION",
    "AUTH_ERROR", "AUTH_REJECTED", "FORBIDDEN", "PENDING", "INACTIVE_ERROR",
})


def make_result(result: str, report: str, **extra: Any) -> dict[str, Any]:
    """构造成一行 JSON 的结果 dict;``needs_attention`` 自动按 result 分类。"""
    out: dict[str, Any] = {
        "result": result,
        "report": report,
        "needs_attention": result in ATTENTION_RESULTS,
    }
    out.update(extra)
    return out


def _dumps(out: dict[str, Any]) -> str:
    """序列化;default=str 兜住意外混入的非 JSON 类型,不让唯一输出通道崩掉。"""
    try:
        return json.dumps(out, ensure_ascii=False, default=str)
    except Exception:
        return repr(out)


def _write_stdout(payload: str) -> None:
    """stdout 写入,pyscriptw 下 sys.stdout 可能为 None;失败降级到调试日志,不抛异常。"""
    stream = getattr(sys, "stdout", None)
    if stream is None:
        return
    try:
        stream.write(payload + "\n")
        stream.flush()
    except Exception as exc:  # noqa: BLE001 — stdout 不可写时不炸输出通道,但要在 stderr 留痕
        log_debug("stdout 写入失败(%s: %s),结果转走日志文件通道" % (type(exc).__name__, exc))


def emit_result(
    out: dict[str, Any],
    *,
    domain: str | None = None,
    action: str | None = None,
    silent: bool = False,
    log_path: Path | None = None,
    sensitive: set[str] | None = None,
    log_errors_without_silent: bool = True,
) -> None:
    """按双通道契约输出结果;本函数保证不抛异常。

    两个通道输出**同一条 JSON Lines 事件记录**,整行是完整 JSON
    (``jq -c 'select(.level=="ERROR")' 日志文件`` 可直接过滤):

    - ``ts``: ISO 8601 带时区;
    - ``level``: ``ERROR``=needs_attention(需要人工),其余 ``INFO``;
    - ``domain``/``action``: 来源域与命令,来自同名参数或 out 里的同名字段,
      两者都没有则省略;
    - 其余字段原样并入(out 的同名字段优先于参数缺省)。

    ``silent``: 写日志文件而非 stdout(定时任务静默运行)。
    ``log_errors_without_silent``: 非静默模式下 ERROR 结果也补写日志(workbuddy
    语义);qoder 语义不需要,传 False。
    """
    record: dict[str, Any] = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "level": "INFO",
    }
    if isinstance(out, dict):
        record.update(out)
        record.setdefault("needs_attention", record.get("result") in ATTENTION_RESULTS)
        if record["needs_attention"]:
            record["level"] = "ERROR"
    else:
        record["payload"] = str(out)
    if domain:
        record["domain"] = domain
    if action:
        record["action"] = action
    payload = _dumps(record)
    for value in sensitive or ():
        payload = payload.replace(value, "[REDACTED]")
    # 台账落库(见 core/store.py): best-effort,任何失败都不影响输出通道;
    # 放在所有输出分支之前,保证静默/非静默/日志兜底每条路径都只落一次库。
    try:
        from auto_signin_kit.core import store

        store.record_payload(payload)
    except Exception:  # noqa: BLE001
        pass
    is_error = isinstance(out, dict) and out.get("result") == "ERROR"
    if not silent:
        _write_stdout(payload)
        if not is_error or not log_errors_without_silent:
            return
    if log_path is None:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    except Exception:
        # 日志写不进去时退回 stdout,至少不把结果丢掉。
        _write_stdout(payload)
