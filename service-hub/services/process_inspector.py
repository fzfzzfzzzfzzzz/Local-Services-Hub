from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import psutil

from .port_scanner import is_port_listening


class ProcessInspectionError(RuntimeError):
    """A target port could not be safely inspected or stopped."""


def pid_for_listening_port(port: int) -> int | None:
    try:
        connections = psutil.net_connections(kind="tcp")
    except (psutil.Error, OSError) as exc:
        raise ProcessInspectionError(f"无法检查端口 {port}：{exc}") from exc
    for connection in connections:
        address = connection.laddr
        if not address or int(address.port) != port:
            continue
        if connection.status != psutil.CONN_LISTEN or not connection.pid:
            continue
        return int(connection.pid)
    return None


def inspect_listening_port(port: int) -> dict[str, Any] | None:
    pid = pid_for_listening_port(port)
    if pid is None:
        return None
    result: dict[str, Any] = {
        "port": port,
        "pid": pid,
        "process_name": None,
        "executable": None,
        "command_line": None,
        "started_at": None,
    }
    try:
        process = psutil.Process(pid)
        with process.oneshot():
            result["process_name"] = process.name() or None
            result["executable"] = process.exe() or None
            command = process.cmdline()
            result["command_line"] = " ".join(command) if command else None
            result["started_at"] = datetime.fromtimestamp(
                process.create_time(),
                timezone.utc,
            ).isoformat()
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        pass
    return result


def stop_confirmed_port_process(port: int, expected_pid: int) -> None:
    actual_pid = pid_for_listening_port(port)
    if actual_pid is None:
        raise ProcessInspectionError(f"端口 {port} 已不再监听，请刷新状态")
    if actual_pid != expected_pid:
        raise ProcessInspectionError(
            f"端口 {port} 的进程已变化（预期 PID {expected_pid}，当前 PID {actual_pid}）"
        )
    if actual_pid == os.getpid():
        raise ProcessInspectionError("拒绝停止 Service Hub 自身进程")

    try:
        process = psutil.Process(actual_pid)
        process.terminate()
        try:
            process.wait(timeout=5)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.Error) as exc:
        raise ProcessInspectionError(f"无法停止 PID {actual_pid}：{exc}") from exc

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not is_port_listening(port):
            return
        time.sleep(0.1)
    raise ProcessInspectionError(f"PID {actual_pid} 已停止，但端口 {port} 尚未释放")
