"""FastAPI 路由: 只编排,零业务逻辑(SPEC-09 §4)。

数据读 = 进程内调 core.store(SQLite 台账)与 schedule 域的 service;
触发 = 经 runner 子进程调 CLI。任何"看起来该在域里做"的判断都不在这里写。

错误口径与 CLI 对齐: 业务失败 HTTP 200 + 业务错误体(exit code 语义放在
body 里),基础设施错误(域不存在/动作不合法/忙)才用 4xx。
"""

from __future__ import annotations

from datetime import datetime
from importlib.metadata import entry_points
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from auto_signin_kit.core import store
from auto_signin_kit.core.adapters.scheduler import get_scheduler
from auto_signin_kit.core.config import load_domain_config_model
from auto_signin_kit.core.report import RESULT_LABELS, RESULT_OK
from auto_signin_kit.domains.schedule import service as schedule_service
from auto_signin_kit.domains.schedule.schema import ScheduleDomainConfig
from auto_signin_kit.ui import runner
from auto_signin_kit.ui.schema import ScheduleOp

router = APIRouter()


def _known_domains() -> tuple[str, ...]:
    """entry_points 注册的业务域名(与 CLI 同源,不硬编码)。"""
    return tuple(sorted(ep.name for ep in entry_points(group="auto_signin_kit.domains")))


def _schedule_config() -> ScheduleDomainConfig:
    return load_domain_config_model("schedule", ScheduleDomainConfig, merge_local=True)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@router.get("/meta/results")
def meta_results() -> dict[str, str]:
    """结果标准词表(core.report.RESULT_LABELS);UI 的标签渲染以这里为准。"""
    return dict(RESULT_LABELS)


@router.get("/overview")
def overview() -> dict[str, Any]:
    """看板首页数据: 每个签到域一张卡(今日状态 + 近 7 天成功占比)。"""
    week = store.daily_status(7)
    today = datetime.now().date().isoformat()
    domains = sorted({str(row["domain"]) for row in week} | set(store.SIGNIN_DOMAINS))
    cards = []
    for domain in domains:
        rows = [r for r in week if r["domain"] == domain]
        today_row = next((r for r in rows if r["day"] == today), None)
        cards.append({
            "domain": domain,
            "today": today_row,
            "week_ok": sum(1 for r in rows if r["result"] in RESULT_OK),
            "week_total": len(rows),
        })
    return {"cards": cards, "generated_at": _now()}


@router.get("/history")
def history(
    days: int = Query(7, ge=1, le=90),
    domain: str | None = Query(None),
    result: str | None = Query(None),
) -> list[dict[str, Any]]:
    """每日折叠视图(每天每域最后一次运行 = 当天最终状态)。"""
    return store.daily_status(days, domain=domain, result=result)


@router.get("/events")
def events(
    date: str | None = Query(None, description="YYYY-MM-DD"),
    domain: str | None = Query(None),
    attention_only: bool = Query(False),
    result: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """运行明细,最新在前;attention_only=1 只看需要人工介入的。"""
    return store.events(
        date=date, domain=domain, attention_only=attention_only,
        result=result, limit=limit)


@router.get("/schedule")
def schedule_status() -> dict[str, Any]:
    """各定时任务注册状态;"未注册"是正常状态,不是错误。"""
    active = get_scheduler()
    tasks = schedule_service.select_tasks(_schedule_config(), None)
    return {"platform": active.platform, "tasks": schedule_service.status(tasks, active)}


def _schedule_op(op_name: str, body: ScheduleOp) -> dict[str, Any]:
    """install/uninstall 共用编排;与 run_action 同一套探测/部分成功语义。"""
    domains = body.domains or []
    if len(domains) > 1:
        raise HTTPException(422, "一次只支持一个域;多域请分次调用")
    domain = domains[0] if domains else None
    if domain is not None and domain not in _known_domains():
        raise HTTPException(404, "未知域: %s" % domain)
    tasks = schedule_service.select_tasks(_schedule_config(), domain)
    tasks, failed = schedule_service.filter_ready(tasks, schedule_service.default_probe)
    active = get_scheduler()
    done, failed2 = (
        schedule_service.install(tasks, active)
        if op_name == "install"
        else schedule_service.uninstall(tasks, active)
    )
    failed = failed + failed2
    return {
        "result": "OK" if not failed else ("PARTIAL" if done else "FAILED"),
        "done": done,
        "failed": failed,
        "platform": active.platform,
    }


@router.post("/schedule/install")
def schedule_install(body: ScheduleOp) -> dict[str, Any]:
    """注册定时任务(幂等,已存在则覆盖);客户端未就绪的任务不注册、如实上报。"""
    return _schedule_op("install", body)


@router.post("/schedule/uninstall")
def schedule_uninstall(body: ScheduleOp) -> dict[str, Any]:
    """注销定时任务(幂等,原本没注册也算成功)。"""
    return _schedule_op("uninstall", body)


@router.post("/run/{domain}")
def run_task(domain: str, action: str = Query("auto")) -> dict[str, Any]:
    """手动触发一个动作(auto/doctor),立即返回 job;前端轮询 /jobs/{id}。"""
    if domain not in _known_domains():
        raise HTTPException(404, "未知域: %s" % domain)
    try:
        job = runner.runner.submit(domain, action)
    except runner.DomainBusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"job": job.public()}


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    """查询一个手动触发的 job;被挤出历史(MAX_JOBS)视为不存在。"""
    job = runner.runner.get(job_id)
    if job is None:
        raise HTTPException(404, "job 不存在或已被挤出历史: %s" % job_id)
    return {"job": job.public()}
