"""子进程运行器: 手动触发的 auto / doctor 在后台线程跑 CLI,前端轮询取结果。

设计要点(SPEC-09 §5):

- **正式结果不靠这里沉淀**: service 的 ``emit_result`` 本来就把每次运行写进
  SQLite 台账,runner 只负责"这次手动点的人等到什么"——job 登记在内存,
  服务进程退出即弃,不与台账争"真相"。
- **白名单**: 只有 :data:`ACTIONS` 里登记的动作能触发,超时上限必须覆盖
  各域时间预算(workbuddy auto ≤540s,见 ``config/domains/workbuddy.yaml``)。
- **同域同动作同时只跑一个**: 签到接口幂等,重复触发无害,但两个进程并发
  写同一份日志/台账会让结果难读,直接 409 拒绝。
"""

from __future__ import annotations

import json
import subprocess
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: 动作白名单 → 子进程超时(秒)。加动作先想清楚超时和输出怎么进台账。
ACTIONS: dict[str, int] = {"auto": 600, "doctor": 120}

#: 内存里最多保留多少条 job 历史(新触发会挤掉最旧的)。
MAX_JOBS = 50

_OUTPUT_TAIL = 4000  # stdout/stderr 留尾巴长度,防一个域刷爆内存


class DomainBusyError(Exception):
    """同域同动作已有任务在跑;HTTP 层转 409。"""


@dataclass
class Job:
    """一次手动触发的完整生命周期;字段被子线程就地更新。"""

    id: str
    domain: str
    action: str
    argv: tuple[str, ...]
    started_at: str
    status: str = "running"  # running / ok / failed / timeout
    exit_code: int | None = None
    output: str = ""
    result: dict[str, Any] | None = None  # stdout 里最后一条可解析的 JSON
    finished_at: str | None = None

    def public(self) -> dict[str, Any]:
        """对外视图;排掉内部字段并保持 JSON 可序列化。"""
        return {
            "id": self.id,
            "domain": self.domain,
            "action": self.action,
            "argv": list(self.argv),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "exit_code": self.exit_code,
            "output": self.output,
            "result": self.result,
        }


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _last_json(text: str) -> dict[str, Any] | None:
    """取 stdout 里**最后**一条能解析的 JSON 行(emit_result 契约是一行一个事件)。"""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


class _Runner:
    """进程级单例;线程池上限 2,防止有人连点把机器拉满。"""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ask-run")
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()

    def submit(self, domain: str, action: str) -> Job:
        """登记并异步启动一个 job;同域同动作在跑则抛 :class:`DomainBusyError`。"""
        timeout = ACTIONS.get(action)
        if timeout is None:
            raise ValueError("不允许的动作: %s(可用: %s)" % (action, "、".join(ACTIONS)))
        with self._lock:
            for job in self._jobs.values():
                if (job.domain == domain and job.action == action
                        and job.status == "running"):
                    raise DomainBusyError(
                        "%s %s 已在运行(job=%s),等它结束再触发" % (domain, action, job.id))
            job = Job(
                id=uuid.uuid4().hex[:12],
                domain=domain,
                action=action,
                argv=("auto-signin-kit", domain, action),
                started_at=_now(),
            )
            self._jobs[job.id] = job
            while len(self._jobs) > MAX_JOBS:
                self._jobs.popitem(last=False)
        self._executor.submit(self._run, job, timeout)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, job: Job, timeout: int) -> None:
        try:
            proc = subprocess.run(  # noqa: S603 — argv 白名单拼装,无外部输入拼接
                job.argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            job.status = "timeout"
            job.output = "超过 %ds 被终止(时间预算见 config/domains)" % timeout
        except OSError as exc:
            job.status = "failed"
            job.output = "启动失败: %s" % exc
        else:
            job.exit_code = proc.returncode
            job.output = ((proc.stdout or "") + (proc.stderr or ""))[-_OUTPUT_TAIL:]
            job.result = _last_json(proc.stdout)
            job.status = "ok" if proc.returncode == 0 else "failed"
        job.finished_at = _now()


#: 进程级运行器实例;app 工厂与路由共用。
runner = _Runner()
