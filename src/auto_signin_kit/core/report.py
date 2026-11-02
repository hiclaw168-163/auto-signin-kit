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

#: **结果取值标准词表**(唯一事实源): 域上报什么、台账存什么、UI 显示什么,
#: 都以这里为准——界面上"有的显示已签到、有的显示 OK"就是因为取值没有
#: 标准答案,各层各翻各的。新增 result 取值必须在这里登记中文标签。
#: 两类语义:
#: - 签到语义(auto/claim/active 的业务结果): CLAIMED/ALREADY/ACTIVE/...
#: - 操作语义(doctor/status 等命令的执行结果): OK/...
RESULT_LABELS = {
    # 签到语义
    "CLAIMED": "领取成功",
    "ALREADY": "今日已签",
    "ACTIVE": "活跃达标",
    "NO_ACTIVITY": "今日未活跃",
    # 操作语义
    "OK": "执行成功",
    "PARTIAL": "部分成功",
    "FAILED": "执行失败",
    # 异常语义
    "NO_AUTH": "登录过期",
    "NETWORK": "网络异常",
    "UNKNOWN": "未知",
}

#: 算"成功"的取值(签到成功/活跃达标/操作成功);聚合统计用,与词表同源。
RESULT_OK = frozenset({"CLAIMED", "ALREADY", "ACTIVE", "OK"})

#: 域内部细粒度码 → 标准词(落库前归一化用)。域代码可以继续用细分码表达
#: 上下文(细分说明放 report 文本),台账只存 10 词,查询聚合才有统一口径;
#: 大小写不敏感,认不出的归 UNKNOWN,绝不让方言进库。
_RESULT_ALIASES = {
    # 认证类 → NO_AUTH
    "NO_SESSION": "NO_AUTH", "AUTH_ERROR": "NO_AUTH", "AUTH_REJECTED": "NO_AUTH",
    "FORBIDDEN": "NO_AUTH", "UNAUTHORIZED": "NO_AUTH", "EXPIRED": "NO_AUTH",
    "NOT_LOGGED_IN": "NO_AUTH", "NO_LOGIN": "NO_AUTH", "SESSION_EXPIRED": "NO_AUTH",
    # 失败类 → FAILED(ERROR 与 FAILED 语义重复,二选一;未激活/限流也归失败,
    # 细节看 report 文本)
    "ERROR": "FAILED", "FAIL": "FAILED", "FAILURE": "FAILED",
    "INACTIVE": "FAILED", "INACTIVE_ERROR": "NO_ACTIVITY",  # 活动不在期≈未活跃
    "RATE_LIMITED": "FAILED", "LIMITED": "FAILED",
    # 网络类 → NETWORK(超时是网络问题的一种)
    "TIMEOUT": "NETWORK", "TIMEOUT_ERROR": "NETWORK",
    "NETWORK_ERROR": "NETWORK", "NET_ERROR": "NETWORK",
    # 待生效/演练/部分提交 → OK
    "PENDING": "OK", "DRY_RUN": "OK",
    # 通用成功措辞 → OK
    "SUCCESS": "OK", "SUCCEEDED": "OK", "DONE": "OK", "FINISHED": "OK",
    # 签到语义
    "SIGNED": "CLAIMED", "SIGNED_IN": "CLAIMED",
    "CHECKED": "CLAIMED", "CHECKED_IN": "CLAIMED",
    "ALREADY_SIGNED": "ALREADY", "ALREADY_CHECKED": "ALREADY",
    "SIGNED_TODAY": "ALREADY", "DUPLICATE": "ALREADY", "REPEAT": "ALREADY",
    "GROWTH": "ACTIVE",  # workbuddy 活跃链 2026-10 前的旧取值
}


def normalize_result(raw: Any) -> str:
    """把任意来源的 result 归一化到标准词表,认不出的按 UNKNOWN 收口。

    台账查询要统一,取值必须收口: 域代码原则上直接用标准词(词表即事实源),
    这里兜住历史遗留与第三方拼写(大小写不敏感);空值/词表外一律 UNKNOWN,
    绝不让域各自的"方言"混进台账。
    """
    value = str(raw or "").strip().upper()
    if not value:
        return "UNKNOWN"
    return _RESULT_ALIASES.get(value, value if value in RESULT_LABELS else "UNKNOWN")


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
    request: dict[str, Any] | None = None,
) -> None:
    """按双通道契约输出结果;本函数保证不抛异常。

    两个通道输出**同一条 JSON Lines 事件记录**,整行是完整 JSON
    (``jq -c 'select(.level=="ERROR")' 日志文件`` 可直接过滤):

    - ``ts``: ISO 8601 带时区(台账落库时转本地无时区时间);
    - ``level``: ``ERROR``=needs_attention(需要人工),其余 ``INFO``;
    - ``domain``/``action``: 来源域与命令,来自同名参数或 out 里的同名字段,
      两者都没有则省略;
    - ``request``: 请求参数 dict(可选参数注入),台账与 response 配对存列;
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
    if request:
        record["request"] = {k: v for k, v in request.items() if k not in record}
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
