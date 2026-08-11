from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import threading
from typing import Any, Iterable


ERROR_PATTERN = re.compile(
    r"(traceback|exception|error|failed|failure|fatal|modulenotfound|"
    r"address already in use|eaddrinuse|cannot find|permission denied)",
    re.IGNORECASE,
)
WARNING_PATTERN = re.compile(r"\b(warn|warning)\b", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _age_seconds(raw_state: dict[str, Any]) -> float:
    age = raw_state.get("age", 0)
    if isinstance(age, bool) or not isinstance(age, (int, float)):
        return 0.0
    return max(0.0, float(age) / 1_000_000_000)


def classify_logs(lines: Iterable[str]) -> dict[str, Any]:
    entries: list[dict[str, str]] = []
    last_error: str | None = None
    for raw_line in lines:
        line = str(raw_line).rstrip("\r\n")
        if ERROR_PATTERN.search(line):
            level = "error"
            stream = "stderr"
            if line.strip():
                last_error = line.strip()[:1000]
        elif WARNING_PATTERN.search(line):
            level = "warning"
            stream = "stderr"
        else:
            level = "info"
            stream = "stdout"
        entries.append({"text": line, "stream": stream, "level": level})
    return {
        "entries": entries,
        "last_error": last_error,
        "stdout_lines": sum(item["stream"] == "stdout" for item in entries),
        "stderr_lines": sum(item["stream"] == "stderr" for item in entries),
    }


class LogArchiveStore:
    def __init__(
        self,
        root: str | Path,
        *,
        max_lines: int = 500,
        max_bytes: int = 1_000_000,
    ) -> None:
        self.root = Path(root)
        self.max_lines = max(50, int(max_lines))
        self.max_bytes = max(10_000, int(max_bytes))
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, service_id: str, name: str) -> Path:
        directory = self.root / service_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory / name

    def _bounded(self, lines: Iterable[str]) -> str:
        selected = [str(line).rstrip("\r\n") for line in lines][-self.max_lines :]
        encoded = ("\n".join(selected) + ("\n" if selected else "")).encode("utf-8")
        if len(encoded) > self.max_bytes:
            encoded = encoded[-self.max_bytes :]
            newline = encoded.find(b"\n")
            if newline >= 0:
                encoded = encoded[newline + 1 :]
        return encoded.decode("utf-8", errors="replace")

    def save_latest(self, service_id: str, lines: Iterable[str]) -> None:
        with self._lock:
            self._path(service_id, "latest.log").write_text(
                self._bounded(lines),
                encoding="utf-8",
                newline="\n",
            )

    def rotate(self, service_id: str, lines: Iterable[str]) -> None:
        with self._lock:
            bounded = self._bounded(lines)
            latest = self._path(service_id, "latest.log")
            if bounded:
                self._path(service_id, "previous.log").write_text(
                    bounded,
                    encoding="utf-8",
                    newline="\n",
                )
            latest.write_text("", encoding="utf-8")

    def read_previous(self, service_id: str) -> list[str]:
        with self._lock:
            path = self._path(service_id, "previous.log")
            if not path.exists():
                return []
            return path.read_text(encoding="utf-8", errors="replace").splitlines()

    def clear_latest(self, service_id: str) -> None:
        with self._lock:
            self._path(service_id, "latest.log").write_text("", encoding="utf-8")

    def remove_service(self, service_id: str) -> None:
        with self._lock:
            directory = self.root / service_id
            if directory.exists():
                shutil.rmtree(directory)


class RunHistoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.temp_path = self.path.with_name(f"{self.path.name}.tmp")
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self._write()
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            services = raw.get("services") if isinstance(raw, dict) else None
            self._data = services if isinstance(services, dict) else {}

    def _write(self) -> None:
        payload = json.dumps(
            {"services": self._data},
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(self.temp_path, self.path)

    @staticmethod
    def _finish_current(
        entry: dict[str, Any],
        *,
        stopped_at: datetime,
        exit_type: str,
        exit_code: int | None,
        last_error: str | None,
    ) -> None:
        current = entry.get("current") if isinstance(entry.get("current"), dict) else {}
        started_at = _parse_time(current.get("started_at")) or stopped_at
        duration = max(0.0, (stopped_at - started_at).total_seconds())
        entry["last_run"] = {
            "started_at": _iso(started_at),
            "stopped_at": _iso(stopped_at),
            "duration_seconds": round(duration, 3),
            "exit_type": exit_type,
            "exit_code": exit_code,
            "pid": current.get("pid"),
            "last_error": last_error or entry.get("last_error"),
        }
        entry["current"] = None
        entry["expected_exit"] = None

    def observe(
        self,
        service_id: str,
        raw_state: dict[str, Any],
        *,
        last_error: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            now = _now()
            entry = self._data.setdefault(service_id, {})
            before = json.dumps(entry, ensure_ascii=False, sort_keys=True)
            running = bool(raw_state.get("is_running", False))
            pid_value = raw_state.get("pid")
            pid = pid_value if isinstance(pid_value, int) and pid_value > 0 else None
            exit_code_value = raw_state.get("exit_code")
            exit_code = exit_code_value if isinstance(exit_code_value, int) else None
            status = str(raw_state.get("status", "")).strip().lower()
            current = entry.get("current") if isinstance(entry.get("current"), dict) else None

            if last_error:
                entry["last_error"] = last_error[:1000]

            if running:
                if current and current.get("pid") != pid:
                    expected = entry.get("expected_exit") in {"stop", "restart"}
                    self._finish_current(
                        entry,
                        stopped_at=now,
                        exit_type="normal_stop" if expected else "abnormal_exit",
                        exit_code=exit_code,
                        last_error=last_error,
                    )
                    current = None
                if current is None:
                    started_at = now - timedelta(seconds=_age_seconds(raw_state))
                    entry["current"] = {
                        "started_at": _iso(started_at),
                        "pid": pid,
                    }
                entry["expected_exit"] = None
            elif current is not None:
                expected = entry.get("expected_exit") in {"stop", "restart"}
                abnormal = (exit_code is not None and exit_code != 0) or status in {
                    "error",
                    "failed",
                    "launch failed",
                    "terminated",
                }
                self._finish_current(
                    entry,
                    stopped_at=now,
                    exit_type=(
                        "normal_stop"
                        if expected or not abnormal
                        else "abnormal_exit"
                    ),
                    exit_code=exit_code,
                    last_error=last_error,
                )
            elif pid is not None and status not in {"", "disabled", "stopped"}:
                previous = entry.get("last_run") if isinstance(entry.get("last_run"), dict) else {}
                if previous.get("pid") != pid:
                    age = _age_seconds(raw_state)
                    stopped_at = now
                    started_at = stopped_at - timedelta(seconds=age)
                    abnormal = (exit_code is not None and exit_code != 0) or status in {
                        "error",
                        "failed",
                        "launch failed",
                        "terminated",
                    }
                    entry["last_run"] = {
                        "started_at": _iso(started_at),
                        "stopped_at": _iso(stopped_at),
                        "duration_seconds": round(age, 3),
                        "exit_type": "abnormal_exit" if abnormal else "normal_stop",
                        "exit_code": exit_code,
                        "pid": pid,
                        "last_error": last_error or entry.get("last_error"),
                    }
            after = json.dumps(entry, ensure_ascii=False, sort_keys=True)
            if after != before:
                self._write()
            last_run = entry.get("last_run")
            return dict(last_run) if isinstance(last_run, dict) else None

    def mark_expected_exit(self, service_id: str, operation: str) -> None:
        with self._lock:
            entry = self._data.setdefault(service_id, {})
            entry["expected_exit"] = operation
            self._write()

    def record_start_failure(self, service_id: str, error: str) -> dict[str, Any]:
        with self._lock:
            now = _now()
            entry = self._data.setdefault(service_id, {})
            record = {
                "started_at": _iso(now),
                "stopped_at": _iso(now),
                "duration_seconds": 0.0,
                "exit_type": "start_failed",
                "exit_code": None,
                "pid": None,
                "last_error": error[:1000],
            }
            entry["last_run"] = record
            entry["last_error"] = error[:1000]
            entry["current"] = None
            self._write()
            return dict(record)

    def set_last_error(self, service_id: str, error: str | None) -> None:
        if not error:
            return
        with self._lock:
            entry = self._data.setdefault(service_id, {})
            entry["last_error"] = error[:1000]
            last_run = entry.get("last_run")
            if isinstance(last_run, dict):
                last_run["last_error"] = error[:1000]
            self._write()

    def get_last_run(self, service_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._data.get(service_id, {})
            last_run = entry.get("last_run") if isinstance(entry, dict) else None
            return dict(last_run) if isinstance(last_run, dict) else None

    def remove_service(self, service_id: str) -> None:
        with self._lock:
            if self._data.pop(service_id, None) is not None:
                self._write()
