from __future__ import annotations

from services.project_state import (
    aggregate_project_state,
    normalize_process_state,
)
from services.registry import ProcessDefinition


def process(*, required: bool = True, grace: int = 10) -> ProcessDefinition:
    return ProcessDefinition(
        id="sample",
        role="web",
        display_name="Web",
        port=8123,
        required=required,
        health_url="http://127.0.0.1:8123/health",
        starting_grace_seconds=grace,
    )


def raw(**overrides):
    value = {
        "status": "Running",
        "is_running": True,
        "is_ready": "Ready",
        "has_ready_probe": True,
        "age": 20_000_000_000,
        "pid": 42,
        "exit_code": 0,
    }
    value.update(overrides)
    return value


def test_normalizes_healthy_process() -> None:
    result = normalize_process_state(process(), raw(), controller_online=True)
    assert result["status"] == "Healthy"
    assert result["pid"] == 42


def test_distinguishes_starting_from_unhealthy() -> None:
    starting = normalize_process_state(
        process(grace=10), raw(is_ready="Not Ready", age=2_000_000_000), controller_online=True
    )
    unhealthy = normalize_process_state(
        process(grace=10), raw(is_ready="Not Ready", age=11_000_000_000), controller_online=True
    )
    assert starting["status"] == "Starting"
    assert unhealthy["status"] == "Unhealthy"


def test_missing_process_is_configuration_error() -> None:
    result = normalize_process_state(process(), None, controller_online=True)
    assert result["status"] == "Error"
    assert "not registered" in result["error"]


def test_offline_is_unknown_not_stopped() -> None:
    result = normalize_process_state(process(), None, controller_online=False)
    assert result["status"] == "Unknown"


def test_aggregate_required_process_states() -> None:
    assert aggregate_project_state([{"required": True, "status": "Stopped"}]) == "Stopped"
    assert aggregate_project_state([{"required": True, "status": "Healthy"}]) == "Healthy"
    assert aggregate_project_state([{"required": True, "status": "Running"}]) == "Running"
    assert (
        aggregate_project_state(
            [
                {"required": True, "status": "Healthy"},
                {"required": True, "status": "Stopped"},
            ]
        )
        == "Partial"
    )
    assert (
        aggregate_project_state(
            [
                {"required": True, "status": "Healthy"},
                {"required": False, "status": "Error"},
            ]
        )
        == "Healthy"
    )

