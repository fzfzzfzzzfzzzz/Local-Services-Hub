from __future__ import annotations

from typing import Any, Iterable

from .registry import ProcessDefinition, ProjectDefinition


ERROR_STATUSES = {
    "error",
    "failed",
    "launch failed",
    "terminated",
    "skipped",
}
STOPPED_STATUSES = {"completed", "disabled", "stopped"}
STARTING_STATUSES = {"pending", "starting", "launching"}


def _age_seconds(raw_state: dict[str, Any]) -> float:
    age = raw_state.get("age", 0)
    if not isinstance(age, (int, float)) or isinstance(age, bool):
        return 0
    # Process Compose exposes age as nanoseconds in its JSON API.
    return max(0.0, float(age) / 1_000_000_000)


def normalize_process_state(
    process: ProcessDefinition,
    raw_state: dict[str, Any] | None,
    *,
    controller_online: bool,
) -> dict[str, Any]:
    base = {
        "id": process.id,
        "role": process.role,
        "display_name": process.display_name,
        "port": process.port,
        "required": process.required,
        "health_url": process.health_url,
        "pid": None,
        "status": "Unknown",
        "health": "Unknown",
        "has_health_check": process.health_url is not None,
        "error": None,
    }

    if not controller_online:
        base["error"] = "Process Compose controller is offline"
        return base

    if raw_state is None:
        base.update(
            status="Error",
            health="Configuration error",
            error=f"Process {process.id} is not registered in Process Compose",
        )
        return base

    raw_status = str(raw_state.get("status", "Unknown"))
    status_key = raw_status.strip().lower()
    is_running = bool(raw_state.get("is_running", False))
    exit_code = raw_state.get("exit_code")
    pid = raw_state.get("pid")
    base["pid"] = pid if isinstance(pid, int) and pid > 0 else None
    has_probe = bool(raw_state.get("has_ready_probe", False))
    base["has_health_check"] = has_probe or process.health_url is not None

    if status_key in ERROR_STATUSES or (
        not is_running
        and isinstance(exit_code, int)
        and exit_code != 0
        and status_key not in STOPPED_STATUSES
    ):
        base.update(status="Error", health="Failed")
        return base

    if not is_running or status_key in STOPPED_STATUSES:
        base.update(status="Stopped", health="Not running")
        return base

    if status_key in STARTING_STATUSES:
        base.update(status="Starting", health="Waiting")
        return base

    if not has_probe:
        base.update(status="Running", health="Unavailable")
        return base

    readiness = str(raw_state.get("is_ready", "")).strip().lower()
    if readiness == "ready":
        base.update(status="Healthy", health="Healthy")
        return base

    if _age_seconds(raw_state) <= process.starting_grace_seconds:
        base.update(status="Starting", health="Waiting")
    else:
        base.update(status="Unhealthy", health="Unhealthy")
    return base


def aggregate_project_state(processes: Iterable[dict[str, Any]]) -> str:
    required = [process for process in processes if process["required"]]
    if not required:
        return "Error"

    statuses = [process["status"] for process in required]
    if "Error" in statuses:
        return "Error"
    if all(status == "Unknown" for status in statuses):
        return "Unknown"
    if "Starting" in statuses:
        return "Starting"
    if all(status == "Stopped" for status in statuses):
        return "Stopped"

    active = {"Running", "Healthy", "Unhealthy"}
    if any(status == "Stopped" for status in statuses) and any(
        status in active for status in statuses
    ):
        return "Partial"
    if "Unhealthy" in statuses:
        return "Unhealthy"
    if all(status == "Healthy" for status in statuses):
        return "Healthy"
    if all(status in {"Healthy", "Running"} for status in statuses):
        return "Running"
    return "Error"


def build_project_view(
    project: ProjectDefinition,
    raw_states: dict[str, dict[str, Any]],
    *,
    controller_online: bool,
) -> dict[str, Any]:
    processes = [
        normalize_process_state(
            process,
            raw_states.get(process.id),
            controller_online=controller_online,
        )
        for process in project.processes
    ]
    errors = [process["error"] for process in processes if process["error"]]
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "category": project.category,
        "namespace": project.namespace,
        "home_url": project.home_url,
        "state": aggregate_project_state(processes),
        "processes": processes,
        "errors": errors,
    }

