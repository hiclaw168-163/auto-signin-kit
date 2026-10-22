"""history 域测试: 台账读写回环 / 回填解析 / 去重,全部落在临时目录。"""

import json
from pathlib import Path

import pytest

from auto_signin_kit.core import store
from auto_signin_kit.domains.history import service


@pytest.fixture(autouse=True)
def _ledger_dir(tmp_path, monkeypatch):
    """台账文件与回填日志都进临时目录,绝不碰真实台账。"""
    monkeypatch.setenv("AUTO_SIGNIN_KIT_LOG_DIR", str(tmp_path / "logs"))
    return tmp_path / "logs"


def _record(domain="trae", result="CLAIMED", ts="2026-10-22T09:05:03+08:00", **extra):
    record = {"ts": ts, "level": "INFO", "domain": domain, "result": result,
              "needs_attention": False, "report": "测试记录", "action": "auto"}
    record.update(extra)
    return record


class TestRecordAndQuery:
    def test_record_payload_lands_in_signin_runs(self):
        assert store.record_payload(json.dumps(_record(), ensure_ascii=False)) is True
        rows = service.events(domain="trae")
        assert len(rows) == 1
        assert rows[0]["result"] == "CLAIMED"

    def test_schedule_domain_routes_to_audit_table(self):
        payload = json.dumps({"ts": "2026-10-22T10:00:00+08:00", "domain": "schedule",
                              "action": "install", "platform": "windows",
                              "done": [{"name": "TraeDailyCheckin"}],
                              "report": "install: 1 个任务已就绪"}, ensure_ascii=False)
        assert store.record_payload(payload) is True
        conn = store.connect()
        try:
            rows = conn.execute("SELECT action, tasks FROM schedule_changes").fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert "TraeDailyCheckin" in rows[0]["tasks"]
        assert service.events(domain="schedule") == []

    def test_invalid_payload_is_rejected_not_stored(self):
        assert store.record_payload("not-json") is False
        assert store.record_payload(json.dumps({"no": "ts"})) is False
        assert service.events(limit=10) == []

    def test_view_folds_multiple_runs_per_day(self):
        for hour, result in (("08:29:14", "NO_ACTIVITY"), ("12:05:03", "ACTIVE")):
            store.record_payload(json.dumps(_record(
                domain="workbuddy", result=result, action="active",
                ts="2026-10-22T%s+08:00" % hour), ensure_ascii=False))
        rows = service.daily_status(days=7, domain="workbuddy")
        assert len(rows) == 1
        assert rows[0]["result"] == "ACTIVE"  # 当天最后一次运行
        assert rows[0]["day"] == "2026-10-22"


def _write_log(log_dir: Path, domain: str, lines: list[str]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / ("%s.log" % domain)).write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestImportLogs:
    def test_backfills_new_and_legacy_formats(self, _ledger_dir):
        _write_log(_ledger_dir, "trae", [
            json.dumps(_record(ts="2026-10-22T09:05:03+08:00"), ensure_ascii=False),
            '[2026-10-21 09:05:03] {"result": "ERROR", "needs_attention": true,'
            ' "code": 9004, "report": "业务失败 code=9004"}',
        ])
        result = service.import_logs(_ledger_dir)
        assert result["per_domain"]["trae"] == 2
        rows = service.events(domain="trae", limit=10)
        assert {row["result"] for row in rows} == {"CLAIMED", "ERROR"}

    def test_reimport_is_deduplicated(self, _ledger_dir):
        _write_log(_ledger_dir, "qoder", [
            json.dumps(_record(domain="qoder", result="ALREADY"), ensure_ascii=False),
        ])
        first = service.import_logs(_ledger_dir)
        second = service.import_logs(_ledger_dir)
        assert first["per_domain"]["qoder"] == 1
        assert second["per_domain"]["qoder"] == 0
        assert second["skipped_duplicates"] == 1

    def test_garbage_lines_are_skipped_without_breaking(self, _ledger_dir):
        _write_log(_ledger_dir, "workbuddy", [
            "这行不是日志",
            "[不是日期] {bad json",
            json.dumps(_record(domain="workbuddy", result="ACTIVE", action="active"),
                       ensure_ascii=False),
        ])
        result = service.import_logs(_ledger_dir)
        assert result["per_domain"]["workbuddy"] == 1

    def test_missing_log_files_are_ok(self, _ledger_dir):
        result = service.import_logs(_ledger_dir / "empty")
        assert result["imported"] == 0
