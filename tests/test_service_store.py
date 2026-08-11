from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.service_store import (
    ActiveConfiguration,
    CorruptStoreError,
    ServiceStore,
    ServiceValidationError,
    validate_service,
)


def service(path: Path, *, port: int = 18801) -> dict[str, object]:
    return {
        "name": "Demo",
        "port": port,
        "working_dir": str(path),
        "command": "python demo.py",
        "url": None,
        "type": "other",
        "note": "",
        "health_url": None,
        "enabled": True,
    }


def test_atomic_write_backup_and_default_url(tmp_path: Path) -> None:
    store_path = tmp_path / "services.json"
    store = ServiceStore(store_path)
    created = store.create_service(service(tmp_path))
    updated = service(tmp_path)
    updated["note"] = "changed"
    store.update_service(created.id, updated)

    assert created.url == "http://127.0.0.1:18801"
    assert (tmp_path / "services.json.bak").is_file()
    assert not (tmp_path / "services.json.tmp").exists()
    assert json.loads(store_path.read_text(encoding="utf-8"))["services"][0]["note"] == "changed"


def test_duplicate_registered_port_is_rejected(tmp_path: Path) -> None:
    store = ServiceStore(tmp_path / "services.json")
    store.create_service(service(tmp_path))
    with pytest.raises(ServiceValidationError, match="已登记"):
        store.create_service(service(tmp_path))


def test_multiple_runtime_items_are_persisted_and_reserve_every_port(tmp_path: Path) -> None:
    store = ServiceStore(tmp_path / "services.json")
    multi = {
        **service(tmp_path, port=18820),
        "type": "fullstack",
        "runtime_items": [
            {"id": "main", "port": 18820, "command": "npm run web"},
            {"id": "api", "port": 18821, "command": "npm run api"},
        ],
        "command": "npm run web",
    }
    created = store.create_service(multi)
    reloaded = ServiceStore(store.path).get_service(created.id)

    assert [(item.id, item.port, item.command) for item in reloaded.items()] == [
        ("main", 18820, "npm run web"),
        ("api", 18821, "npm run api"),
    ]
    assert reloaded.port == 18820
    assert reloaded.command == "npm run web"
    with pytest.raises(ServiceValidationError, match="18821.*已登记"):
        store.create_service(service(tmp_path, port=18821))


def test_legacy_single_port_service_gets_one_default_runtime_item(tmp_path: Path) -> None:
    created = ServiceStore(tmp_path / "services.json").create_service(
        service(tmp_path, port=18822)
    )

    assert len(created.items()) == 1
    assert created.items()[0].id == "main"
    assert created.items()[0].port == 18822
    assert created.items()[0].command == "python demo.py"


def test_corrupt_primary_loads_backup_read_only_and_restores_explicitly(tmp_path: Path) -> None:
    path = tmp_path / "services.json"
    store = ServiceStore(path)
    store.create_service(service(tmp_path))
    # A second valid write creates services.json.bak.
    current = store.list_services()[0]
    store.update_service(current.id, service(tmp_path))
    path.write_text("{broken", encoding="utf-8")

    degraded = ServiceStore(path)
    assert degraded.using_backup is True
    assert degraded.list_services()
    with pytest.raises(CorruptStoreError):
        degraded.create_service(service(tmp_path, port=18802))

    degraded.restore_backup()
    assert degraded.using_backup is False
    assert json.loads(path.read_text(encoding="utf-8"))["services"]


def test_pending_active_configuration_is_persisted_and_can_be_cleared(
    tmp_path: Path,
) -> None:
    path = tmp_path / "services.json"
    store = ServiceStore(path)
    created = store.create_service(service(tmp_path, port=18803))
    active = ActiveConfiguration(
        port=18803,
        working_dir=str(tmp_path),
        command="python demo.py",
        url="http://127.0.0.1:18803",
        health_url=None,
    )
    changed = service(tmp_path, port=18804)
    store.update_service(created.id, changed, active_config=active)

    reloaded = ServiceStore(path).get_service(created.id)
    assert reloaded.port == 18804
    assert reloaded.active_config == active

    store.set_active_config(created.id, None)
    assert store.get_service(created.id).active_config is None


def test_health_check_defaults_preserve_legacy_services(tmp_path: Path) -> None:
    tcp_service = validate_service(
        service(tmp_path, port=18805),
        existing=[],
        service_id="tcp_service",
    )
    worker = validate_service(
        {**service(tmp_path, port=18806), "type": "worker"},
        existing=[tcp_service],
        service_id="worker",
    )
    http_service = validate_service(
        {
            **service(tmp_path, port=18807),
            "health_url": "http://127.0.0.1:18807/health",
        },
        existing=[tcp_service, worker],
        service_id="http_service",
    )

    assert tcp_service.health_check_type == "tcp"
    assert worker.health_check_type == "process"
    assert http_service.health_check_type == "http"
    assert http_service.health_expected_status == 200


def test_http_health_check_requires_url_and_valid_expected_status(tmp_path: Path) -> None:
    missing_url = {
        **service(tmp_path, port=18808),
        "health_check_type": "http",
    }
    invalid_status = {
        **missing_url,
        "health_url": "http://127.0.0.1:18808/health",
        "health_expected_status": 700,
    }

    with pytest.raises(ServiceValidationError, match="health_url"):
        validate_service(missing_url, existing=[], service_id="missing_url")
    with pytest.raises(ServiceValidationError, match="100–599"):
        validate_service(invalid_status, existing=[], service_id="invalid_status")


def test_dependency_cycle_and_dependent_delete_are_rejected(tmp_path: Path) -> None:
    store = ServiceStore(tmp_path / "services.json")
    backend = store.create_service(service(tmp_path, port=18809))
    frontend_payload = {
        **service(tmp_path, port=18810),
        "name": "Frontend",
        "dependencies": [backend.id],
    }
    frontend = store.create_service(frontend_payload)

    backend_payload = {
        **service(tmp_path, port=18809),
        "dependencies": [frontend.id],
    }
    with pytest.raises(ServiceValidationError, match="循环依赖"):
        store.update_service(backend.id, backend_payload)
    with pytest.raises(ServiceValidationError, match="仍被以下服务依赖"):
        store.delete_service(backend.id)
