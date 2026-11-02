"""core.store 测试: 台账读写回环 / 折叠视图 / done_today / compact,全部落在临时目录。"""

import json
from datetime import datetime

from auto_signin_kit.core import store


def _record(domain="trae", result="CLAIMED", ts="2026-10-22T09:05:03+08:00", **extra):
    record = {"ts": ts, "level": "INFO", "domain": domain, "result": result,
              "needs_attention": False, "report": "测试记录", "action": "auto"}
    record.update(extra)
    return record


def _today_ts(hour: str) -> str:
    today = datetime.now().astimezone().date().isoformat()
    return "%sT%s+08:00" % (today, hour)


def _insert_backfill(ts: str, domain: str = "trae", result: str = "CLAIMED") -> None:
    """直插一行 backfill 行(record_payload 固定 source=run,回填行只能 SQL 构造)。"""
    conn = store.connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO ask_signin_runs (executed_at, domain, action, source,"
                " result, needs_attention, report, detail)"
                " VALUES (?, ?, 'backfill', 'backfill', ?, 0, 'x', '{}')",
                (ts, domain, result))
    finally:
        conn.close()


class TestRecordAndQuery:
    def test_record_payload_lands_in_signin_runs(self):
        assert store.record_payload(json.dumps(_record(), ensure_ascii=False)) is True
        rows = store.events(domain="trae")
        assert len(rows) == 1
        assert rows[0]["result"] == "CLAIMED"
        assert rows[0]["source"] == "run"  # 正常执行,规范标识
        assert rows[0]["action"] == "auto"

    def test_source_filter_separates_run_and_backfill(self):
        store.record_payload(json.dumps(_record(), ensure_ascii=False))
        _insert_backfill("2026-10-21T12:00:00+08:00")
        assert len(store.events(source="run")) == 1
        assert len(store.events(source="backfill")) == 1
        assert store.events() == store.events(source="run") + store.events(source="backfill")

    def test_record_payload_normalizes_result_dialects(self):
        # 落库前归一化: 域报 "success" 这类方言,库里的必须是标准词 OK
        store.record_payload(json.dumps(_record(result="success"), ensure_ascii=False))
        rows = store.events(domain="trae")
        assert rows[0]["result"] == "OK"

    def test_fine_grained_words_collapse_into_slim_vocab(self):
        # 词表 19→10: 域内部细粒度码落库时坍缩(AUTH_REJECTED→NO_AUTH),
        # 细节说明留在 report 文本里
        store.record_payload(json.dumps(_record(result="AUTH_REJECTED"), ensure_ascii=False))
        rows = store.events(domain="trae")
        assert rows[0]["result"] == "NO_AUTH"

    def test_executed_at_stored_as_local_naive(self):
        # 时区不逐行落库: 带 +08:00 的 ts 落库转本地裸时间,SQL date() 直接可用
        store.record_payload(json.dumps(_record(ts="2026-10-22T09:05:03+08:00"),
                                        ensure_ascii=False))
        rows = store.events(domain="trae")
        assert rows[0]["executed_at"] == "2026-10-22 09:05:03"

    def test_request_response_paired_columns(self):
        # 请求参数与响应结果一一对应: request= 注入 + 业务返回字段自动进 response
        record = _record(result="CLAIMED", silent=True, today_credit=100,
                         streak_days=34, request={"dry_run": False})
        store.record_payload(json.dumps(record, ensure_ascii=False))
        row = store.events(domain="trae")[0]
        assert json.loads(row["request"]) == {"dry_run": False}
        resp = json.loads(row["response"])
        assert resp["silent"] is True
        assert resp["today_credit"] == 100 and resp["streak_days"] == 34
        assert "result" not in resp and "report" not in resp  # 信封字段不混入

    def test_result_filter_normalizes_and_matches(self):
        store.record_payload(json.dumps(_record(result="success"), ensure_ascii=False))
        # 查询侧同样归一化: 用方言查和标准词查,结果一致
        assert len(store.events(result="success")) == 1
        assert len(store.events(result="OK")) == 1
        assert store.events(result="NO_AUTH") == []
        # 折叠视图同口径
        assert len(store.daily_status(days=30, result="OK")) == 1
        assert store.daily_status(days=30, result="CLAIMED") == []

    def test_daily_view_files_early_morning_runs_on_local_day(self):
        # 回归: 旧版 executed_at 带时区后缀,裸 date() 转 UTC 后凌晨 0~8 点的运行
        # 会被记到"昨天",台账看起来就像调度没跑(2026-11-03 真实踩坑)。
        # 落库时已转本地裸时间,视图的 date() 直接取本地日期
        ts = "2026-11-03T00:05:00+08:00"
        store.record_payload(json.dumps(_record(ts=ts, result="CLAIMED"),
                                        ensure_ascii=False))
        expected_day = datetime.fromisoformat(ts).astimezone().date().isoformat()
        rows = store.daily_status(days=3650)
        assert len(rows) == 1
        assert rows[0]["day"] == expected_day
        assert rows[0]["executed_at"] == "2026-11-03 00:05:00"

    def test_done_today_counts_today_runs(self):
        ts = datetime.now().astimezone().isoformat(timespec="seconds")
        store.record_payload(json.dumps(_record(ts=ts, result="CLAIMED"),
                                        ensure_ascii=False))
        assert store.done_today("trae") is True
        store.record_payload(json.dumps(_record(ts=ts, result="NO_AUTH"),
                                        ensure_ascii=False))
        assert store.done_today("qoder") is False  # 失败行不算"已签"

    def test_schedule_domain_routes_to_audit_table(self):
        payload = json.dumps({"ts": "2026-10-22T10:00:00+08:00", "domain": "schedule",
                              "action": "install", "platform": "windows",
                              "done": [{"name": "TraeDailyCheckin"}],
                              "report": "install: 1 个任务已就绪"}, ensure_ascii=False)
        assert store.record_payload(payload) is True
        conn = store.connect()
        try:
            rows = conn.execute("SELECT action, tasks FROM ask_schedule_changes").fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert "TraeDailyCheckin" in rows[0]["tasks"]
        assert store.events(domain="schedule") == []

    def test_invalid_payload_is_rejected_not_stored(self):
        assert store.record_payload("not-json") is False
        assert store.record_payload(json.dumps({"no": "ts"})) is False
        assert store.events(limit=10) == []

    def test_view_folds_multiple_runs_per_day(self):
        # 用"今天"构造时间戳: 视图按 now()-7 天过滤,硬编码日期会随时间推移
        # 掉出窗口(2026-11-02 实际发生过)。
        today = datetime.now().astimezone().date().isoformat()
        for hour, result in (("08:29:14", "NO_ACTIVITY"), ("12:05:03", "ACTIVE")):
            store.record_payload(json.dumps(_record(
                domain="workbuddy", result=result, action="active",
                ts="%sT%s+08:00" % (today, hour)), ensure_ascii=False))
        rows = store.daily_status(days=7, domain="workbuddy")
        assert len(rows) == 1
        assert rows[0]["result"] == "ACTIVE"  # 当天最后一次运行
        assert rows[0]["day"] == today


class TestDoneTodayAndCompact:
    """调度跳过判断(done_today)与历史冗余清理(compact_history)。"""

    def test_done_today_requires_success_run_today(self):
        assert store.done_today("trae") is False
        # 失败行不算成功
        store.record_payload(json.dumps(_record(
            domain="trae", result="NO_AUTH", needs_attention=True,
            ts=_today_ts("09:00")), ensure_ascii=False))
        assert store.done_today("trae") is False
        store.record_payload(json.dumps(_record(
            domain="trae", result="ALREADY", ts=_today_ts("10:00")),
            ensure_ascii=False))
        assert store.done_today("trae") is True

    def test_done_today_ignores_other_day_source_and_domain(self):
        store.record_payload(json.dumps(_record(  # 昨天,不算今天
            ts="2026-01-01T09:05:03+08:00"), ensure_ascii=False))
        _insert_backfill(_today_ts("09:05:03"))  # backfill 来源不算真实执行
        store.record_payload(json.dumps(_record(  # 别的域
            domain="qoder", ts=_today_ts("09:05:03")), ensure_ascii=False))
        assert store.done_today("trae") is False

    def test_compact_keeps_first_success_last_row_and_failures(self):
        rows = [
            ("08:00", "CLAIMED", False),   # 首条成功 → 保留
            ("10:00", "ALREADY", False),   # 冗余成功 → 删
            ("13:00", "ALREADY", False),   # 冗余成功 → 删
            ("19:00", "CLAIMED", False),   # 冗余成功 → 删
            ("20:00", "NO_AUTH", True),    # 失败行 → 保留
            ("23:00", "ALREADY", False),   # 当天最后一行 → 保留(视图取它)
        ]
        for hour, result, attention in rows:
            store.record_payload(json.dumps(_record(
                result=result, needs_attention=attention,
                ts=_today_ts(hour)), ensure_ascii=False))
        result = store.compact_history()
        assert result["deleted"] == 3
        assert result["per_domain"] == {"trae": 3}
        remaining = [row["result"] for row in store.events(domain="trae", limit=10)]
        assert remaining == ["ALREADY", "NO_AUTH", "CLAIMED"]  # 最新在前: 末行/失败/首成功
        assert store.compact_history()["deleted"] == 0  # 幂等

    def test_compact_keeps_backfill_and_isolates_by_day_domain(self):
        _insert_backfill("2026-01-01T09:05:03+08:00")  # backfill 行不清理
        store.record_payload(json.dumps(_record(  # 昨天的成功行不属于今天组,首条即保留
            ts="2026-01-01T10:05:03+08:00"), ensure_ascii=False))
        assert store.compact_history()["deleted"] == 0
        assert len(store.events(limit=10)) == 2
