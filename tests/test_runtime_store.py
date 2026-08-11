from __future__ import annotations

from services.runtime_store import LogArchiveStore, RunHistoryStore, classify_logs


def test_log_diagnosis_and_rotation(tmp_path) -> None:
    store = LogArchiveStore(tmp_path / "logs", max_lines=3)
    lines = ["starting", "warning: slow", "ModuleNotFoundError: openpyxl"]
    diagnosis = classify_logs(lines)
    store.rotate("demo", lines)

    assert diagnosis["last_error"] == "ModuleNotFoundError: openpyxl"
    assert diagnosis["stderr_lines"] == 2
    assert store.read_previous("demo") == lines


def test_run_history_records_abnormal_exit(tmp_path) -> None:
    store = RunHistoryStore(tmp_path / "service-runs.json")
    running = {
        "status": "Running",
        "is_running": True,
        "pid": 123,
        "age": 5_000_000_000,
        "exit_code": 0,
    }
    stopped = {
        "status": "Completed",
        "is_running": False,
        "pid": 123,
        "age": 8_000_000_000,
        "exit_code": 1,
    }
    assert store.observe("demo", running) is None
    record = store.observe("demo", stopped, last_error="boom")

    assert record is not None
    assert record["exit_type"] == "abnormal_exit"
    assert record["exit_code"] == 1
    assert record["pid"] == 123
    assert record["last_error"] == "boom"
