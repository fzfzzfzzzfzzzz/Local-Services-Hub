from __future__ import annotations

from pathlib import Path

import pytest

from services.service_store import ActiveConfiguration, validate_service
from services.status_resolver import StatusResolver


def definition(path: Path, *, port: int = 18901):
    return validate_service(
        {
            "name": "Demo",
            "port": port,
            "working_dir": str(path),
            "command": "python demo.py",
            "url": None,
            "type": "other",
            "note": "",
            "health_url": None,
            "enabled": True,
        },
        existing=[],
        service_id="demo",
    )


@pytest.mark.asyncio
async def test_managed_and_offline_states(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("services.status_resolver.is_port_listening", lambda _: True)
    resolver = StatusResolver()
    try:
        managed = await resolver.resolve_one(
            definition(tmp_path),
            {"status": "Running", "is_running": True, "pid": 55},
            controller_online=True,
        )
        offline = await resolver.resolve_one(
            definition(tmp_path),
            None,
            controller_online=False,
        )
    finally:
        await resolver.close()
    assert managed["state"] == "Managed Running"
    assert managed["started_at"] is not None
    assert managed["uptime_seconds"] == 0.0
    assert offline["state"] == "Unknown"


@pytest.mark.asyncio
async def test_multi_port_state_requires_every_runtime_port(tmp_path: Path, monkeypatch) -> None:
    service = validate_service(
        {
            **definition(tmp_path, port=18920).to_dict(),
            "port": 18920,
            "command": "npm run web",
            "health_check_type": "process",
            "runtime_items": [
                {"id": "main", "port": 18920, "command": "npm run web"},
                {"id": "api", "port": 18921, "command": "npm run api"},
            ],
        },
        existing=[],
        service_id="multi_demo",
    )
    listening = {18920: True, 18921: False}
    monkeypatch.setattr(
        "services.status_resolver.is_port_listening",
        lambda port: listening[port],
    )
    states = {
        "service_multi_demo": {
            "status": "Running",
            "is_running": True,
            "pid": 101,
            "age": 20_000_000_000,
        },
        "service_multi_demo__api": {
            "status": "Running",
            "is_running": True,
            "pid": 102,
            "age": 20_000_000_000,
        },
    }
    resolver = StatusResolver()
    try:
        partial = await resolver.resolve_service(
            service,
            states,
            controller_online=True,
        )
        listening[18921] = True
        healthy = await resolver.resolve_service(
            service,
            states,
            controller_online=True,
        )
    finally:
        await resolver.close()

    assert partial["state"] == "Unhealthy"
    assert ":18921" in partial["error"]
    assert healthy["state"] == "Managed Running"
    assert healthy["health_check"]["detail"] == "2/2 个运行项端口正常"


@pytest.mark.asyncio
async def test_running_process_without_listening_port_becomes_unhealthy_after_grace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("services.status_resolver.is_port_listening", lambda _: False)
    service = definition(tmp_path, port=18908)
    resolver = StatusResolver(startup_grace_seconds=15)
    try:
        starting = await resolver.resolve_one(
            service,
            {"status": "Running", "is_running": True, "pid": 124, "age": 2_000_000_000},
            controller_online=True,
        )
        failed = await resolver.resolve_one(
            service,
            {"status": "Running", "is_running": True, "pid": 124, "age": 20_000_000_000},
            controller_online=True,
        )
    finally:
        await resolver.close()

    assert starting["state"] == "Starting"
    assert failed["state"] == "Unhealthy"
    assert failed["error"] == "TCP 端口 18908 未监听"
    assert failed["health_check"]["status"] == "failing"


@pytest.mark.asyncio
async def test_disabled_state_does_not_depend_on_controller(tmp_path: Path) -> None:
    enabled = definition(tmp_path, port=18907)
    disabled = validate_service(
        {**enabled.to_dict(), "enabled": False},
        existing=[],
        service_id="demo",
    )
    resolver = StatusResolver()
    try:
        view = await resolver.resolve_one(
            disabled,
            {"status": "Running", "is_running": True, "pid": 123},
            controller_online=False,
        )
    finally:
        await resolver.close()

    assert view["state"] == "Disabled"
    assert view["pid"] is None
    assert view["error"] is None


@pytest.mark.asyncio
async def test_sort_order_is_stable_within_status(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("services.status_resolver.is_port_listening", lambda _: False)
    resolver = StatusResolver()
    first = definition(tmp_path, port=18902)
    second = validate_service(
        {**first.to_dict(), "name": "Second", "port": 18903},
        existing=[first],
        service_id="second",
    )
    try:
        views = await resolver.resolve_all(
            [first, second],
            {},
            controller_online=True,
        )
    finally:
        await resolver.close()
    assert [item["id"] for item in views] == ["demo", "second"]
    assert all(item["state"] == "Stopped" for item in views)


@pytest.mark.asyncio
async def test_health_failure_uses_startup_grace_before_unhealthy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = definition(tmp_path, port=18904)
    service = validate_service(
        {
            **service.to_dict(),
            "health_check_type": "http",
            "health_url": "http://127.0.0.1:18904/health",
        },
        existing=[],
        service_id="demo",
    )
    resolver = StatusResolver(startup_grace_seconds=15)

    async def unhealthy(_: str, expected_status: int) -> tuple[bool, str]:
        assert expected_status == 200
        return False, "HTTP 返回 503，预期 200"

    monkeypatch.setattr(resolver, "_http_health", unhealthy)
    try:
        starting = await resolver.resolve_one(
            service,
            {"status": "Running", "is_running": True, "pid": 61, "age": 3_000_000_000},
            controller_online=True,
        )
        unhealthy_result = await resolver.resolve_one(
            service,
            {"status": "Running", "is_running": True, "pid": 61, "age": 16_000_000_000},
            controller_online=True,
        )
    finally:
        await resolver.close()

    assert starting["state"] == "Starting"
    assert starting["error"] is None
    assert unhealthy_result["state"] == "Unhealthy"
    assert unhealthy_result["error"] == "HTTP 返回 503，预期 200"
    assert unhealthy_result["health_check"]["status"] == "failing"


@pytest.mark.asyncio
async def test_pending_restart_uses_active_runtime_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    desired = definition(tmp_path, port=18906)
    desired = validate_service(
        {
            **desired.to_dict(),
            "url": "http://127.0.0.1:18906",
            "health_check_type": "http",
            "health_url": "http://127.0.0.1:18906/health",
            "active_config": ActiveConfiguration(
                port=18905,
                working_dir=str(tmp_path),
                command="python old.py",
                url="http://127.0.0.1:18905",
                health_url="http://127.0.0.1:18905/health",
                health_check_type="http",
            ).to_dict(),
        },
        existing=[],
        service_id="demo",
    )
    requested: list[str] = []
    resolver = StatusResolver()

    async def healthy(url: str, expected_status: int) -> tuple[bool, str]:
        requested.append(url)
        assert expected_status == 200
        return True, "HTTP 200，符合预期"

    monkeypatch.setattr(resolver, "_http_health", healthy)
    try:
        view = await resolver.resolve_one(
            desired,
            {"status": "Running", "is_running": True, "pid": 77, "age": 20_000_000_000},
            controller_online=True,
        )
    finally:
        await resolver.close()

    assert view["pending_restart"] is True
    assert view["effective_port"] == 18905
    assert view["effective_url"] == "http://127.0.0.1:18905"
    assert requested == ["http://127.0.0.1:18905/health"]


@pytest.mark.asyncio
async def test_process_health_mode_does_not_require_a_listening_port(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("services.status_resolver.is_port_listening", lambda _: False)
    service = validate_service(
        {
            **definition(tmp_path, port=18909).to_dict(),
            "type": "worker",
            "health_check_type": "process",
        },
        existing=[],
        service_id="worker",
    )
    resolver = StatusResolver(startup_grace_seconds=0)
    try:
        view = await resolver.resolve_one(
            service,
            {"status": "Running", "is_running": True, "pid": 91, "age": 2_000_000_000},
            controller_online=True,
        )
    finally:
        await resolver.close()

    assert view["state"] == "Managed Running"
    assert view["health_check"] == {
        "type": "process",
        "status": "passing",
        "target": "PID 91",
        "expected_status": None,
        "detail": "Process Compose 进程正在运行",
    }
