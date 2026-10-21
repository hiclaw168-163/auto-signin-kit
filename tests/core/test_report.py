"""JSON Lines 事件契约测试: ts/level/domain 标准字段、双通道同构、脱敏不回归。"""

import json
from datetime import datetime
from pathlib import Path

from auto_signin_kit.core import report


def _read_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_log_line_is_pure_jsonl_with_standard_fields(tmp_path):
    """日志行必须是完整 JSON(时间戳在字段里,不再拼在行首),ts 带时区可解析。"""
    log = tmp_path / "signin.log"
    report.emit_result(
        report.make_result("ALREADY", "已签过"), domain="workbuddy",
        silent=True, log_path=log,
    )
    (record,) = _read_records(log)
    ts = datetime.fromisoformat(record["ts"])
    assert ts.tzinfo is not None
    assert record["level"] == "INFO"
    assert record["domain"] == "workbuddy"
    assert record["result"] == "ALREADY"
    assert record["needs_attention"] is False


def test_needs_attention_maps_to_error_level(tmp_path):
    """needs_attention 结果的 level 必须是 ERROR,过滤定位才有意义。"""
    log = tmp_path / "signin.log"
    report.emit_result(
        report.make_result("NO_AUTH", "未登录"), domain="workbuddy",
        silent=True, log_path=log,
    )
    (record,) = _read_records(log)
    assert record["level"] == "ERROR"
    assert record["needs_attention"] is True


def test_stdout_and_file_carry_the_same_record(tmp_path, capsys):
    """双通道同一条记录: stdout 给机器读,日志文件给事后排查读,来源字段都在。"""
    log = tmp_path / "qoder.log"
    report.emit_result(
        {"result": "ERROR", "report": "boom"}, domain="qoder",
        silent=False, log_path=log, log_errors_without_silent=True,
    )
    (file_record,) = _read_records(log)
    stdout_record = json.loads(capsys.readouterr().out)
    assert file_record == stdout_record
    assert file_record["domain"] == "qoder"
    assert file_record["level"] == "ERROR"


def test_action_flows_into_record_from_out_or_kwarg(tmp_path):
    """action 来源两路均可: out 自带字段或调用参数,参数优先。"""
    log = tmp_path / "trae.log"
    report.emit_result({"result": "OK", "action": "auto"}, domain="trae",
                       silent=True, log_path=log)
    report.emit_result({"result": "OK"}, domain="trae", action="install",
                       silent=True, log_path=log)
    first, second = _read_records(log)
    assert first["action"] == "auto"
    assert second["action"] == "install"


def test_sensitive_values_redacted_in_record(tmp_path):
    """脱敏在 JSONL 化之后依然生效,整行找不到原始敏感值。"""
    log = tmp_path / "signin.log"
    report.emit_result(
        {"result": "OK", "token": "secret-value"}, domain="workbuddy",
        silent=True, log_path=log, sensitive={"secret-value"},
    )
    (record,) = _read_records(log)
    assert record["token"] == "[REDACTED]"
    assert "secret-value" not in log.read_text(encoding="utf-8")
