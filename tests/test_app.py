from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app import create_app
from services.config_generator import ProcessComposeConfigGenerator, process_id_for
from services.process_compose import ControllerOffline, ProcessComposeError
from services.service_store import ServiceStore, ServiceStoreError


class FakeProcessCompose:
    base_url = "http://127.0.0.1:8751"

    def __init__(self) -> None:
        self.operations: list[tuple[str, str]] = []
        self.states: dict[str, dict[str, Any]] = {}
        self.refuse_stop = False
        self.reload_error: Exception | None = None

    async def list_processes(self) -> dict[str, dict[str, Any]]:
        return self.states

    async def is_online(self) -> bool:
        return True

    async def reload_configuration(self) -> None:
        self.operations.append(("reload", "configuration"))
        if self.reload_error is not None:
            raise self.reload_error

    async def start_process(self, process_id: str) -> None:
        self.operations.append(("start", process_id))
        self.states[process_id] = {
            "name": process_id,
            "status": "Running",
            "is_running": True,
            "pid": 4321,
            "exit_code": 0,
        }

    async def stop_process(self, process_id: str) -> None:
        self.operations.append(("stop", process_id))
        if self.refuse_stop:
            return
        self.states[process_id] = {
            "name": process_id,
            "status": "Disabled",
            "is_running": False,
            "pid": 0,
            "exit_code": 0,
        }

    async def restart_process(self, process_id: str) -> None:
        self.operations.append(("restart", process_id))

    async def get_logs(self, process_id: str, *, limit: int) -> list[str]:
        return [f"{process_id} log"][-limit:]

    async def clear_logs(self, process_id: str) -> None:
        self.operations.append(("clear_logs", process_id))


def payload(working_dir: Path, *, port: int = 18421) -> dict[str, Any]:
    return {
        "name": "Sample",
        "port": port,
        "working_dir": str(working_dir),
        "command": f"python server.py --port {port}",
        "url": f"http://127.0.0.1:{port}",
        "type": "backend",
        "note": "test service",
        "health_url": None,
        "health_check_type": "tcp",
        "health_expected_status": 200,
        "enabled": True,
    }


def make_client(tmp_path: Path) -> tuple[TestClient, FakeProcessCompose, ServiceStore, Path]:
    store = ServiceStore(tmp_path / "services.json")
    generated = tmp_path / "process-compose.generated.yaml"
    fake = FakeProcessCompose()
    app = create_app(
        store=store,
        process_compose=fake,
        generator=ProcessComposeConfigGenerator(generated),
    )
    return TestClient(app), fake, store, generated


def test_health_and_crud_persist_and_sync_config(tmp_path: Path) -> None:
    client, fake, store, generated = make_client(tmp_path)
    with client:
        health = client.get("/health")
        created = client.post("/api/services", json=payload(tmp_path))
        service_id = created.json()["service"]["id"]
        listed = client.get("/api/services")
        fetched = client.get(f"/api/services/{service_id}")
        updated_payload = payload(tmp_path)
        updated_payload["note"] = "updated"
        updated = client.put(f"/api/services/{service_id}", json=updated_payload)
        deleted = client.delete(f"/api/services/{service_id}")

    assert health.status_code == 200
    assert health.json()["version"] == "1.0.0"
    assert created.status_code == 201
    assert created.json()["service"]["health_check_type"] == "tcp"
    assert created.json()["service"]["health_check"]["type"] == "tcp"
    assert listed.json()["services"][0]["name"] == "Sample"
    assert fetched.json()["id"] == service_id
    assert updated.json()["service"]["note"] == "updated"
    assert deleted.status_code == 200
    assert store.list_services() == []
    assert "AUTO-GENERATED" in generated.read_text(encoding="utf-8")
    assert ("reload", "configuration") in fake.operations
    assert json.loads((tmp_path / "services.json").read_text(encoding="utf-8")) == {"services": []}


def test_start_stop_restart_and_logs_are_service_id_scoped(tmp_path: Path) -> None:
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18422))
    process_id = process_id_for(service.id)
    with client:
        started = client.post(f"/api/services/{service.id}/start")
        restarted = client.post(f"/api/services/{service.id}/restart")
        logs = client.get(f"/api/services/{service.id}/logs")
        stopped = client.post(f"/api/services/{service.id}/stop")
        missing = client.post("/api/services/not_registered/start")

    assert started.status_code == 200
    assert restarted.status_code == 200
    assert stopped.status_code == 200
    assert logs.json()["logs"] == [f"{process_id} log"]
    assert missing.status_code == 404
    # A restart is executed as a confirmed stop plus a clean start.
    assert fake.operations.count(("start", process_id)) == 2
    assert fake.operations.count(("stop", process_id)) == 2
    assert ("restart", process_id) not in fake.operations


def test_restart_of_externally_restarted_service_offers_takeover(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18439))
    process_id = process_id_for(service.id)
    fake.states[process_id] = {
        "name": process_id,
        "status": "Completed",
        "is_running": False,
        "pid": 8765,
        "exit_code": 0,
    }
    monkeypatch.setattr("services.status_resolver.is_port_listening", lambda _: True)

    with client:
        restarted = client.post(f"/api/services/{service.id}/restart")

    assert restarted.status_code == 409
    assert restarted.json() == {
        "error": "external_running",
        "detail": "服务已由外部程序重启，请重新纳入管理",
        "state": "External Running",
        "can_takeover": True,
    }
    assert ("restart", process_id) not in fake.operations
    assert ("stop", process_id) not in fake.operations
    assert ("start", process_id) not in fake.operations


def test_restart_reports_port_conflict_when_port_grabbed_after_stop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18449))
    process_id = process_id_for(service.id)
    fake.states[process_id] = {
        "name": process_id,
        "status": "Running",
        "is_running": True,
        "pid": 4321,
        "exit_code": 0,
    }
    occupied = {"value": False}

    def fake_listening(port: int) -> bool:
        return occupied["value"]

    def fake_inspect(port: int) -> dict[str, Any]:
        return {
            "port": port,
            "pid": 999,
            "process_name": "other.exe",
            "executable": None,
            "command_line": "other.exe --serve",
            "started_at": None,
        }

    hub = client.app.state.hub
    original_confirm = hub.run_history.confirm_normal_stop

    def confirm_then_occupy(service_id: str, pid: int | None = None) -> None:
        # The intruder binds the port right after the stop is confirmed.
        occupied["value"] = True
        original_confirm(service_id, pid)

    monkeypatch.setattr("services.hub.is_port_listening", fake_listening)
    monkeypatch.setattr("services.hub.inspect_listening_port", fake_inspect)
    monkeypatch.setattr(hub.run_history, "confirm_normal_stop", confirm_then_occupy)

    with client:
        restarted = client.post(f"/api/services/{service.id}/restart")
        after = client.get(f"/api/services/{service.id}")

    body = restarted.json()
    assert restarted.status_code == 409
    assert body["error"] == "port_conflict"
    assert "other.exe" in body["detail"]
    assert "PID 999" in body["detail"]
    assert body["port_conflict"]["pid"] == 999
    assert ("stop", process_id) in fake.operations
    assert ("start", process_id) not in fake.operations
    assert after.json()["state"] == "Stopped"
    assert after.json()["last_run"]["exit_type"] == "start_failed"


def test_stop_external_confirms_pids_before_stopping(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18443))
    process_id = process_id_for(service.id)
    stopped: list[tuple[int, int]] = []

    def fake_inspect(port: int) -> dict[str, Any]:
        return {
            "port": port,
            "pid": 8765,
            "process_name": "node.exe",
            "executable": None,
            "command_line": "node server.js",
            "started_at": None,
        }

    def fake_stop(port: int, pid: int) -> None:
        stopped.append((port, pid))

    monkeypatch.setattr("services.hub.inspect_listening_port", fake_inspect)
    monkeypatch.setattr("services.hub.stop_confirmed_port_process", fake_stop)

    with client:
        refused = client.post(
            f"/api/services/{service.id}/stop-external",
            json={"confirm": True, "pids": [8765]},
        )
        monkeypatch.setattr("services.status_resolver.is_port_listening", lambda _: True)
        preview = client.post(
            f"/api/services/{service.id}/stop-external",
            json={"confirm": False},
        )
        wrong_pid = client.post(
            f"/api/services/{service.id}/stop-external",
            json={"confirm": True, "pids": [9999]},
        )
        confirmed = client.post(
            f"/api/services/{service.id}/stop-external",
            json={"confirm": True, "pids": [8765]},
        )

    assert refused.status_code == 409
    assert refused.json()["error"] == "not_external"
    assert preview.status_code == 200
    assert preview.json()["requires_confirmation"] is True
    assert preview.json()["processes"][0]["pid"] == 8765
    assert wrong_pid.status_code == 409
    assert wrong_pid.json()["error"] == "pid_confirmation_required"
    assert confirmed.status_code == 200
    assert confirmed.json()["stopped_pids"] == [8765]
    assert stopped == [(18443, 8765)]
    assert ("start", process_id) not in fake.operations


def test_stop_external_handles_every_runtime_port(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, fake, store, _ = make_client(tmp_path)
    multi_payload = payload(tmp_path, port=18444)
    multi_payload.update(
        health_check_type="process",
        runtime_items=[
            {"id": "main", "port": 18444, "command": "npm run web"},
            {"id": "api", "port": 18445, "command": "npm run api"},
        ],
    )
    service = store.create_service(multi_payload)
    for process_id in (process_id_for(service.id), process_id_for(service.id, "api")):
        fake.states[process_id] = {
            "name": process_id,
            "status": "Completed",
            "is_running": False,
            "pid": 100,
            "exit_code": 0,
        }
    stopped: list[tuple[int, int]] = []

    def fake_inspect(port: int) -> dict[str, Any]:
        return {
            "port": port,
            "pid": 5000 + port % 100,
            "process_name": "node.exe",
            "executable": None,
            "command_line": None,
            "started_at": None,
        }

    def fake_stop(port: int, pid: int) -> None:
        stopped.append((port, pid))

    monkeypatch.setattr("services.status_resolver.is_port_listening", lambda _: True)
    monkeypatch.setattr("services.hub.inspect_listening_port", fake_inspect)
    monkeypatch.setattr("services.hub.stop_confirmed_port_process", fake_stop)

    with client:
        preview = client.post(
            f"/api/services/{service.id}/stop-external",
            json={"confirm": False},
        )
        pids = sorted(process["pid"] for process in preview.json()["processes"])
        confirmed = client.post(
            f"/api/services/{service.id}/stop-external",
            json={"confirm": True, "pids": pids},
        )

    processes = preview.json()["processes"]
    assert sorted(process["port"] for process in processes) == [18444, 18445]
    assert confirmed.status_code == 200
    assert sorted(stopped) == sorted(
        (process["port"], process["pid"]) for process in processes
    )


def test_logs_for_external_instance_fall_back_to_archive(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18446))
    process_id = process_id_for(service.id)
    fake.states[process_id] = {
        "name": process_id,
        "status": "Completed",
        "is_running": False,
        "pid": 8765,
        "exit_code": 0,
    }
    monkeypatch.setattr("services.status_resolver.is_port_listening", lambda _: True)

    async def broken_logs(process_id: str, *, limit: int) -> list[str]:
        raise ProcessComposeError("process is not supervised by the controller")

    monkeypatch.setattr(fake, "get_logs", broken_logs)
    archive_dir = tmp_path / "runtime" / "logs" / service.id
    archive_dir.mkdir(parents=True)
    (archive_dir / "previous.log").write_text("archived output\n", encoding="utf-8")

    with client:
        logs = client.get(f"/api/services/{service.id}/logs")

    assert logs.status_code == 200
    assert logs.json()["logs"] == []
    assert logs.json()["previous_entries"][0]["text"] == "archived output"


def test_confirmed_stop_is_recorded_as_normal_even_with_rough_exit_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18447))
    process_id = process_id_for(service.id)
    fake.states[process_id] = {
        "name": process_id,
        "status": "Running",
        "is_running": True,
        "pid": 4321,
        "exit_code": 0,
    }

    async def rough_stop(target: str) -> None:
        fake.operations.append(("stop", target))
        fake.states[target] = {
            "name": target,
            "status": "Terminated",
            "is_running": False,
            "pid": 4321,
            "exit_code": 1,
        }

    monkeypatch.setattr(fake, "stop_process", rough_stop)

    with client:
        before = client.get(f"/api/services/{service.id}")
        stopped = client.post(f"/api/services/{service.id}/stop")
        after = client.get(f"/api/services/{service.id}")

    assert stopped.status_code == 200
    assert before.json()["state"] == "Starting"  # TCP grace window, still managed
    assert after.json()["state"] == "Stopped"
    assert after.json()["error"] is None
    assert after.json()["last_error"] is None
    assert after.json()["pid"] is None
    assert after.json()["pids"] == []
    assert all(item["state"] == "Stopped" for item in after.json()["runtime_views"])
    assert all(item["error"] is None for item in after.json()["runtime_views"])
    assert after.json()["last_run"]["exit_type"] == "normal_stop"
    assert after.json()["last_run"]["exit_code"] == 1


def test_clear_last_run_dismisses_the_exit_summary(tmp_path: Path) -> None:
    client, _, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18448))

    with client:
        client.app.state.hub.run_history.record_start_failure(
            service.id, "command failed immediately"
        )
        before = client.get(f"/api/services/{service.id}")
        cleared = client.delete(f"/api/services/{service.id}/last-run")
        after = client.get(f"/api/services/{service.id}")

    assert before.json()["last_run"]["exit_type"] == "start_failed"
    assert cleared.status_code == 200
    assert cleared.json()["cleared"] is True
    assert after.json()["last_run"] is None


def test_crash_residue_keeps_error_until_dismissed(tmp_path: Path) -> None:
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18450))
    process_id = process_id_for(service.id)
    running_state = {
        "name": process_id,
        "status": "Running",
        "is_running": True,
        "pid": 777,
        "exit_code": 0,
    }
    fake.states[process_id] = dict(running_state)

    with client:
        observed = client.get(f"/api/services/{service.id}")
        fake.states[process_id] = {
            "name": process_id,
            "status": "Terminated",
            "is_running": False,
            "pid": 777,
            "exit_code": 1,
        }
        crashed = client.get(f"/api/services/{service.id}")
        client.delete(f"/api/services/{service.id}/last-run")
        dismissed = client.get(f"/api/services/{service.id}")

    assert observed.json()["state"] == "Starting"  # TCP grace window
    assert crashed.json()["state"] == "Error"
    assert crashed.json()["last_run"]["exit_type"] == "abnormal_exit"
    assert dismissed.json()["state"] == "Stopped"
    assert dismissed.json()["error"] is None


def test_confirmed_stop_with_nonzero_exit_code_still_relaxes(tmp_path: Path) -> None:
    """Windows kills report exit code 1; a confirmed stop must not stay red."""
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18451))
    process_id = process_id_for(service.id)
    fake.states[process_id] = {
        "name": process_id,
        "status": "Completed",
        "is_running": False,
        "pid": 888,
        "exit_code": 1,
    }

    with client:
        client.app.state.hub.run_history.mark_expected_exit(service.id, "stop")
        observed = client.get(f"/api/services/{service.id}")

    assert observed.json()["state"] == "Stopped"
    assert observed.json()["error"] is None
    assert observed.json()["last_run"]["exit_type"] == "normal_stop"
    assert observed.json()["last_run"]["exit_code"] == 1


def test_stop_during_startup_records_normal_stop_for_unobserved_process(
    tmp_path: Path,
) -> None:
    """A stop before any snapshot caught the process running must not be an abnormal exit."""
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18452))
    process_id = process_id_for(service.id)
    fake.states[process_id] = {
        "name": process_id,
        "status": "Running",
        "is_running": True,
        "pid": 909,
        "exit_code": 0,
    }

    with client:
        stopped = client.post(f"/api/services/{service.id}/stop")
        after = client.get(f"/api/services/{service.id}")

    assert stopped.status_code == 200
    assert after.json()["state"] == "Stopped"
    assert after.json()["last_run"]["exit_type"] == "normal_stop"


def test_multi_port_service_manages_every_runtime_process(tmp_path: Path) -> None:
    client, fake, _, generated = make_client(tmp_path)
    multi_payload = payload(tmp_path, port=18440)
    multi_payload.update(
        type="fullstack",
        health_check_type="process",
        command="npm run web",
        runtime_items=[
            {"id": "main", "port": 18440, "command": "npm run web"},
            {"id": "api", "port": 18441, "command": "npm run api"},
        ],
    )

    with client:
        created = client.post("/api/services", json=multi_payload)
        service_id = created.json()["service"]["id"]
        started = client.post(f"/api/services/{service_id}/start")
        restarted = client.post(f"/api/services/{service_id}/restart")
        logs = client.get(f"/api/services/{service_id}/logs")
        cleared = client.delete(f"/api/services/{service_id}/logs")
        stopped = client.post(f"/api/services/{service_id}/stop")

    primary = process_id_for(service_id)
    api = process_id_for(service_id, "api")
    assert created.status_code == 201
    assert [item["port"] for item in created.json()["service"]["runtime_items"]] == [18440, 18441]
    assert started.status_code == 200
    assert restarted.status_code == 200
    assert stopped.status_code == 200
    assert ("stop", primary) in fake.operations
    assert ("stop", api) in fake.operations
    assert fake.operations.count(("start", primary)) == 2
    assert fake.operations.count(("start", api)) == 2
    assert any("[:18440]" in line for line in logs.json()["logs"])
    assert any("[:18441]" in line for line in logs.json()["logs"])
    assert cleared.status_code == 200
    generated_text = generated.read_text(encoding="utf-8")
    assert f"{primary}:" in generated_text
    assert f"{api}:" in generated_text


def test_running_critical_edit_and_delete_require_explicit_choice(tmp_path: Path) -> None:
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18423))
    process_id = process_id_for(service.id)
    fake.states[process_id] = {
        "name": process_id,
        "status": "Running",
        "is_running": True,
        "pid": 321,
        "exit_code": 0,
    }
    changed = payload(tmp_path, port=18424)
    with client:
        edit_without_choice = client.put(f"/api/services/{service.id}", json=changed)
        edit_deferred = client.put(
            f"/api/services/{service.id}?restart=false",
            json=changed,
        )
        delete_without_stop = client.delete(f"/api/services/{service.id}")
        deleted = client.delete(f"/api/services/{service.id}?stop=true")

    assert edit_without_choice.status_code == 409
    assert edit_without_choice.json()["error"] == "restart_decision_required"
    assert edit_deferred.status_code == 200
    assert edit_deferred.json()["restart_deferred"] is True
    deferred_service = edit_deferred.json()["service"]
    assert deferred_service["pending_restart"] is True
    assert deferred_service["active_config"]["port"] == 18423
    assert deferred_service["port"] == 18424
    assert deferred_service["effective_port"] == 18423
    assert delete_without_stop.status_code == 409
    assert deleted.status_code == 200
    assert ("stop", process_id) in fake.operations


def test_restart_applies_deferred_config_and_clears_pending_state(tmp_path: Path) -> None:
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18426))
    process_id = process_id_for(service.id)
    fake.states[process_id] = {
        "name": process_id,
        "status": "Running",
        "is_running": True,
        "pid": 456,
        "exit_code": 0,
    }
    changed = payload(tmp_path, port=18427)
    with client:
        deferred = client.put(
            f"/api/services/{service.id}?restart=false",
            json=changed,
        )
        restarted = client.post(f"/api/services/{service.id}/restart")
        after = client.get(f"/api/services/{service.id}")

    assert deferred.json()["service"]["pending_restart"] is True
    assert restarted.status_code == 200
    assert after.json()["pending_restart"] is False
    assert "active_config" not in after.json()
    assert after.json()["effective_port"] == 18427
    assert store.get_service(service.id).active_config is None
    assert fake.operations.count(("stop", process_id)) == 1
    assert fake.operations.count(("start", process_id)) == 1


def test_rejects_reserved_port_and_missing_directory(tmp_path: Path) -> None:
    client, _, _, _ = make_client(tmp_path)
    reserved = payload(tmp_path, port=8750)
    missing = payload(tmp_path / "missing", port=18425)
    with client:
        reserved_response = client.post("/api/services", json=reserved)
        missing_response = client.post("/api/services", json=missing)
    assert reserved_response.status_code == 422
    assert missing_response.status_code == 422


def test_port_conflict_returns_structured_process_diagnostics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18429))
    monkeypatch.setattr("services.hub.is_port_listening", lambda _: True)
    monkeypatch.setattr(
        "services.hub.inspect_listening_port",
        lambda port: {
            "port": port,
            "pid": 9876,
            "process_name": "python.exe",
            "executable": "C:/Python/python.exe",
            "command_line": "python server.py",
            "started_at": "2026-08-11T01:00:00+00:00",
        },
    )

    with client:
        response = client.post(f"/api/services/{service.id}/start")

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "port_conflict"
    assert body["port_conflict"]["port"] == 18429
    assert body["port_conflict"]["pid"] == 9876
    assert body["port_conflict"]["process_name"] == "python.exe"
    assert body["port_conflict"]["registered_service"]["id"] == service.id
    assert body["port_conflict"]["can_start"] is False


def test_dependency_start_order_and_group_crud(tmp_path: Path) -> None:
    client, fake, store, _ = make_client(tmp_path)
    backend_payload = payload(tmp_path, port=18430)
    backend_payload["name"] = "Backend"
    backend_payload["health_check_type"] = "process"
    backend = store.create_service(backend_payload)
    frontend_payload = payload(tmp_path, port=18431)
    frontend_payload["name"] = "Frontend"
    frontend_payload["health_check_type"] = "process"
    frontend_payload["dependencies"] = [backend.id]
    frontend = store.create_service(frontend_payload)

    with client:
        created_group = client.post(
            "/api/groups",
            json={
                "name": "Demo Scene",
                "description": "Backend + Frontend",
                "services": [frontend.id],
            },
        )
        group_id = created_group.json()["group"]["id"]
        listed = client.get("/api/groups")
        started = client.post(f"/api/groups/{group_id}/start")
        updated = client.put(
            f"/api/groups/{group_id}",
            json={
                "name": "Updated Scene",
                "description": "",
                "services": [backend.id, frontend.id],
            },
        )
        deleted = client.delete(f"/api/groups/{group_id}")

    assert created_group.status_code == 201
    assert listed.json()["groups"][0]["service_details"][0]["name"] == "Frontend"
    assert [item["id"] for item in started.json()["plan"]] == [backend.id, frontend.id]
    assert [item for item in fake.operations if item[0] == "start"] == [
        ("start", process_id_for(backend.id)),
        ("start", process_id_for(frontend.id)),
    ]
    assert updated.json()["group"]["name"] == "Updated Scene"
    assert deleted.status_code == 200


def test_logs_include_diagnostics_and_can_be_cleared(tmp_path: Path) -> None:
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18432))

    async def diagnostic_logs(process_id: str, *, limit: int) -> list[str]:
        return ["starting", "ModuleNotFoundError: openpyxl"][-limit:]

    fake.get_logs = diagnostic_logs  # type: ignore[method-assign]
    with client:
        logs = client.get(f"/api/services/{service.id}/logs")
        cleared = client.delete(f"/api/services/{service.id}/logs")
        current = client.get(f"/api/services/{service.id}")

    assert logs.json()["last_error"] == "ModuleNotFoundError: openpyxl"
    assert logs.json()["stderr_lines"] == 1
    assert logs.json()["entries"][-1]["stream"] == "stderr"
    assert logs.json()["current_last_error"] is None
    assert current.json()["state"] == "Stopped"
    assert current.json()["last_error"] is None
    assert cleared.json()["previous_preserved"] is True
    assert ("clear_logs", process_id_for(service.id)) in fake.operations


def test_logs_fall_back_to_last_run_error_when_output_has_no_error_text(tmp_path: Path) -> None:
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18433))
    client.app.state.hub.run_history.record_start_failure(
        service.id,
        "Process Compose 状态：Completed（Exit Code 1）",
    )

    async def ordinary_logs(process_id: str, *, limit: int) -> list[str]:
        return ["server started", "server stopped"][-limit:]

    fake.get_logs = ordinary_logs  # type: ignore[method-assign]
    with client:
        logs = client.get(f"/api/services/{service.id}/logs")

    assert logs.status_code == 200
    assert logs.json()["last_error"] == "Process Compose 状态：Completed（Exit Code 1）"


def test_disabled_service_has_explicit_state_and_no_runtime_actions(tmp_path: Path) -> None:
    client, _, store, generated = make_client(tmp_path)
    disabled_payload = payload(tmp_path, port=18428)
    disabled_payload["enabled"] = False
    with client:
        created = client.post("/api/services", json=disabled_payload)
        service_id = created.json()["service"]["id"]
        snapshot = client.get("/api/services")
        start = client.post(f"/api/services/{service_id}/start")
        logs = client.get(f"/api/services/{service_id}/logs")

    assert created.status_code == 201
    assert created.json()["service"]["state"] == "Disabled"
    assert snapshot.json()["summary"]["disabled"] == 1
    assert start.status_code == 409
    assert start.json()["error"] == "service_disabled"
    assert logs.status_code == 409
    assert process_id_for(service_id) not in generated.read_text(encoding="utf-8")
    assert store.get_service(service_id).enabled is False


def test_running_service_must_stop_and_release_port_before_disable(tmp_path: Path) -> None:
    client, fake, store, generated = make_client(tmp_path)
    with client:
        created = client.post("/api/services", json=payload(tmp_path, port=18429))
        service_id = created.json()["service"]["id"]
        process_id = process_id_for(service_id)
        fake.states[process_id] = {
            "name": process_id,
            "status": "Running",
            "is_running": True,
            "pid": 789,
            "exit_code": 0,
        }
        disabled_payload = payload(tmp_path, port=18429)
        disabled_payload["enabled"] = False
        missing_confirmation = client.put(
            f"/api/services/{service_id}",
            json=disabled_payload,
        )
        disabled = client.put(
            f"/api/services/{service_id}?restart=true",
            json=disabled_payload,
        )

    assert missing_confirmation.status_code == 409
    assert missing_confirmation.json()["error"] == "stop_decision_required"
    assert disabled.status_code == 200
    assert disabled.json()["disabled_after_stop"] is True
    assert disabled.json()["service"]["state"] == "Disabled"
    assert ("stop", process_id) in fake.operations
    assert process_id not in generated.read_text(encoding="utf-8")
    assert store.get_service(service_id).enabled is False


def test_config_generation_failure_keeps_json_and_yaml_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _, store, generated = make_client(tmp_path)
    json_before = store.path.read_bytes()
    yaml_before = generated.read_bytes()

    def fail_prepare(_: list[Any]) -> Path:
        raise ServiceStoreError("simulated generator failure")

    monkeypatch.setattr(client.app.state.hub.generator, "prepare", fail_prepare)
    with client:
        response = client.post("/api/services", json=payload(tmp_path, port=18430))

    assert response.status_code == 500
    assert store.list_services() == []
    assert store.path.read_bytes() == json_before
    assert generated.read_bytes() == yaml_before
    assert not store.temp_path.exists()


def test_controller_offline_marks_committed_configuration_pending(tmp_path: Path) -> None:
    client, fake, store, generated = make_client(tmp_path)
    fake.reload_error = ControllerOffline("controller unavailable")
    with client:
        created = client.post("/api/services", json=payload(tmp_path, port=18431))
        snapshot = client.get("/api/services")

    assert created.status_code == 201
    assert created.json()["config_sync_warning"] == "controller unavailable"
    assert snapshot.json()["configuration"] == {
        "sync_pending": True,
        "sync_error": "controller unavailable",
    }
    service_id = created.json()["service"]["id"]
    assert store.get_service(service_id).port == 18431
    assert process_id_for(service_id) in generated.read_text(encoding="utf-8")


def test_stop_and_delete_timeout_keeps_registration_and_logs(tmp_path: Path) -> None:
    client, fake, store, generated = make_client(tmp_path)
    with client:
        created = client.post("/api/services", json=payload(tmp_path, port=18432))
        service_id = created.json()["service"]["id"]
        process_id = process_id_for(service_id)
        fake.states[process_id] = {
            "name": process_id,
            "status": "Running",
            "is_running": True,
            "pid": 991,
            "exit_code": 0,
        }
        fake.refuse_stop = True
        client.app.state.hub._stop_timeout_seconds = 0.03
        deleted = client.delete(f"/api/services/{service_id}?stop=true")
        logs = client.get(f"/api/services/{service_id}/logs")

    assert deleted.status_code == 409
    assert deleted.json()["error"] == "stop_confirmation_timeout"
    assert "已保留登记和日志" in deleted.json()["detail"]
    assert store.get_service(service_id).id == service_id
    assert process_id in generated.read_text(encoding="utf-8")
    assert logs.status_code == 200
    assert logs.json()["logs"] == [f"{process_id} log"]


def test_shutdown_endpoint_stops_only_service_hub_after_response(tmp_path: Path) -> None:
    client, fake, _, _ = make_client(tmp_path)
    client.app.state.hub._hub_shutdown_delay_seconds = 0
    with client:
        response = client.post("/api/hub/shutdown")

    assert response.status_code == 202
    assert response.json() == {
        "accepted": True,
        "message": "Service Hub 正在关闭；业务服务将继续运行",
    }
    assert fake.operations == [("stop", "service_hub")]


def test_restart_endpoint_restarts_only_service_hub_after_response(tmp_path: Path) -> None:
    client, fake, _, _ = make_client(tmp_path)
    client.app.state.hub._hub_shutdown_delay_seconds = 0
    with client:
        response = client.post("/api/hub/restart")

    assert response.status_code == 202
    assert response.json() == {
        "accepted": True,
        "instance_id": client.app.state.instance_id,
        "message": "Service Hub 正在重启；业务服务将继续运行",
    }
    assert fake.operations == [("restart", "service_hub")]


def test_open_service_directory_uses_registered_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18433))
    opened: list[str] = []
    monkeypatch.setattr("services.hub._open_directory", opened.append)

    with client:
        response = client.post(f"/api/services/{service.id}/open-directory")

    assert response.status_code == 200
    assert response.json() == {
        "service_id": service.id,
        "opened": str(tmp_path.resolve()),
    }
    assert opened == [str(tmp_path.resolve())]


def test_first_launch_failure_stays_a_current_issue(tmp_path: Path) -> None:
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18501))
    process_id = process_id_for(service.id)
    fake.states[process_id] = {
        "name": process_id,
        "status": "Launch Failed",
        "is_running": False,
        "pid": 0,
        "exit_code": 1,
        "launch_error": "command could not be started",
    }

    with client:
        snapshot = client.get("/api/services").json()

    current = snapshot["services"][0]
    assert current["state"] == "Error"
    assert current["pid"] is None
    assert current["pids"] == []
    assert current["last_error"] == "command could not be started"
    assert snapshot["summary"]["issues"] == 1


def test_dismissed_exit_only_suppresses_the_same_process_pid(tmp_path: Path) -> None:
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18502))
    process_id = process_id_for(service.id)
    fake.states[process_id] = {
        "name": process_id,
        "status": "Terminated",
        "is_running": False,
        "pid": 701,
        "exit_code": 1,
    }

    with client:
        failed = client.get(f"/api/services/{service.id}").json()
        cleared = client.delete(f"/api/services/{service.id}/last-run")
        same_pid = client.get(f"/api/services/{service.id}").json()
        fake.states[process_id] = {
            **fake.states[process_id],
            "pid": 702,
            "launch_error": "new process failed",
        }
        new_pid = client.get(f"/api/services/{service.id}").json()

    assert failed["state"] == "Error"
    assert cleared.status_code == 200
    assert same_pid["state"] == "Stopped"
    assert same_pid["last_error"] is None
    assert same_pid["pid"] is None
    assert same_pid["runtime_views"][0]["state"] == "Stopped"
    assert new_pid["state"] == "Error"
    assert new_pid["last_error"] == "new process failed"


def test_multi_runtime_source_combinations_have_consistent_summaries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, fake, store, _ = make_client(tmp_path)
    raw = payload(tmp_path, port=18503)
    raw.update(
        health_check_type="process",
        runtime_items=[
            {"id": "main", "port": 18503, "command": "npm run web"},
            {"id": "api", "port": 18504, "command": "npm run api"},
        ],
    )
    service = store.create_service(raw)
    primary = process_id_for(service.id)
    api = process_id_for(service.id, "api")
    listening: set[int] = {18504}

    monkeypatch.setattr(
        "services.status_resolver.is_port_listening",
        lambda port: port in listening,
    )
    monkeypatch.setattr(
        "services.hub.inspect_listening_port",
        lambda port: {
            "port": port,
            "pid": 7000 + port,
            "process_name": "node.exe",
            "executable": None,
            "command_line": f"node --port {port}",
            "started_at": None,
        },
    )
    fake.states[primary] = {
        "name": primary,
        "status": "Running",
        "is_running": True,
        "pid": 301,
        "exit_code": 0,
    }
    fake.states[api] = {
        "name": api,
        "status": "Stopped",
        "is_running": False,
        "pid": 0,
        "exit_code": 0,
    }

    with client:
        managed_external = client.get("/api/services").json()
        fake.states[primary] = {
            "name": primary,
            "status": "Completed",
            "is_running": False,
            "pid": 401,
            "exit_code": 0,
        }
        listening.clear()
        listening.add(18503)
        external_stopped = client.get("/api/services").json()
        fake.states[api] = {
            "name": api,
            "status": "Launch Failed",
            "is_running": False,
            "pid": 402,
            "exit_code": 1,
            "launch_error": "api failed",
        }
        external_error = client.get("/api/services").json()
        listening.add(18504)
        all_external = client.get("/api/services").json()

    for snapshot in (managed_external, external_stopped, external_error):
        assert snapshot["services"][0]["state"] == "Mixed Running"
        assert snapshot["summary"]["mixed"] == 1
        assert snapshot["summary"]["external"] == 0
        assert snapshot["summary"]["issues"] == 1
    assert all_external["services"][0]["state"] == "External Running"
    assert all_external["summary"]["mixed"] == 0
    assert all_external["summary"]["external"] == 1


def test_mixed_takeover_revalidates_and_starts_every_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, fake, store, _ = make_client(tmp_path)
    raw = payload(tmp_path, port=18505)
    raw.update(
        health_check_type="process",
        runtime_items=[
            {"id": "main", "port": 18505, "command": "npm run web"},
            {"id": "api", "port": 18506, "command": "npm run api"},
        ],
    )
    service = store.create_service(raw)
    primary = process_id_for(service.id)
    api = process_id_for(service.id, "api")
    fake.states[primary] = {
        "name": primary,
        "status": "Running",
        "is_running": True,
        "pid": 411,
        "exit_code": 0,
    }
    fake.states[api] = {
        "name": api,
        "status": "Completed",
        "is_running": False,
        "pid": 0,
        "exit_code": 0,
    }
    listening = {18506: True}
    stopped: list[tuple[int, int]] = []

    def is_listening(port: int) -> bool:
        return listening.get(port, False)

    def inspect(port: int) -> dict[str, Any] | None:
        if not is_listening(port):
            return None
        return {
            "port": port,
            "pid": 912,
            "process_name": "node.exe",
            "executable": "C:/node.exe",
            "command_line": "node api.js",
            "started_at": None,
        }

    def stop_external(port: int, pid: int) -> None:
        stopped.append((port, pid))
        listening[port] = False

    monkeypatch.setattr("services.status_resolver.is_port_listening", is_listening)
    monkeypatch.setattr("services.hub.is_port_listening", is_listening)
    monkeypatch.setattr("services.hub.inspect_listening_port", inspect)
    monkeypatch.setattr("services.hub.stop_confirmed_port_process", stop_external)

    with client:
        preview = client.post(
            f"/api/services/{service.id}/takeover",
            json={"confirm": False},
        )
        confirmed = client.post(
            f"/api/services/{service.id}/takeover",
            json={"confirm": True, "pids": [411, 912, 912]},
        )

    processes = preview.json()["processes"]
    assert [(item["port"], item["source"]) for item in processes] == [
        (18505, "managed"),
        (18506, "external"),
    ]
    assert confirmed.status_code == 200
    assert confirmed.json()["stopped_pids"] == [411, 912]
    assert stopped == [(18506, 912)]
    assert ("stop", primary) in fake.operations
    assert ("start", primary) in fake.operations
    assert ("start", api) in fake.operations


def test_takeover_pid_change_returns_stale_without_stopping_anything(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, fake, store, _ = make_client(tmp_path)
    raw = payload(tmp_path, port=18507)
    raw.update(
        health_check_type="process",
        runtime_items=[
            {"id": "main", "port": 18507, "command": "npm run web"},
            {"id": "api", "port": 18508, "command": "npm run api"},
        ],
    )
    service = store.create_service(raw)
    current_pid = {"value": 921}
    stopped: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "services.status_resolver.is_port_listening",
        lambda port: port == 18507,
    )
    monkeypatch.setattr(
        "services.hub.inspect_listening_port",
        lambda port: {
            "port": port,
            "pid": current_pid["value"],
            "process_name": "node.exe",
            "executable": None,
            "command_line": "node web.js",
            "started_at": None,
        },
    )
    monkeypatch.setattr(
        "services.hub.stop_confirmed_port_process",
        lambda port, pid: stopped.append((port, pid)),
    )

    with client:
        preview = client.post(
            f"/api/services/{service.id}/takeover",
            json={"confirm": False},
        ).json()
        current_pid["value"] = 922
        stale = client.post(
            f"/api/services/{service.id}/takeover",
            json={"confirm": True, "pids": [921]},
        )

    assert stale.status_code == 409
    assert stale.json()["error"] == "runtime_confirmation_stale"
    assert stopped == []
    assert not [item for item in fake.operations if item[0] in {"start", "stop"}]
    assert preview["processes"][0]["pid"] == 921


def test_takeover_source_change_with_same_pids_is_stale(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, fake, store, _ = make_client(tmp_path)
    raw = payload(tmp_path, port=18513)
    raw.update(
        health_check_type="process",
        runtime_items=[
            {"id": "main", "port": 18513, "command": "npm run web"},
            {"id": "api", "port": 18514, "command": "npm run api"},
        ],
    )
    service = store.create_service(raw)
    primary = process_id_for(service.id)
    api = process_id_for(service.id, "api")
    listening = {18514}
    external_pids = {18513: 1001, 18514: 1002}
    fake.states[primary] = {
        "status": "Running",
        "is_running": True,
        "pid": 1001,
        "exit_code": 0,
    }
    fake.states[api] = {
        "status": "Stopped",
        "is_running": False,
        "pid": 0,
        "exit_code": 0,
    }
    monkeypatch.setattr(
        "services.status_resolver.is_port_listening",
        lambda port: port in listening,
    )
    monkeypatch.setattr(
        "services.hub.inspect_listening_port",
        lambda port: {
            "port": port,
            "pid": external_pids[port],
            "process_name": "node.exe",
            "executable": None,
            "command_line": "node app.js",
            "started_at": None,
        },
    )

    with client:
        preview = client.post(
            f"/api/services/{service.id}/takeover",
            json={"confirm": False},
        )
        fake.states[primary] = {
            "status": "Stopped",
            "is_running": False,
            "pid": 0,
            "exit_code": 0,
        }
        fake.states[api] = {
            "status": "Running",
            "is_running": True,
            "pid": 1002,
            "exit_code": 0,
        }
        listening.clear()
        listening.add(18513)
        stale = client.post(
            f"/api/services/{service.id}/takeover",
            json={"confirm": True, "pids": [1001, 1002]},
        )

    assert preview.status_code == 200
    assert stale.status_code == 409
    assert stale.json()["error"] == "runtime_confirmation_stale"
    assert not [item for item in fake.operations if item[0] in {"start", "stop"}]


def test_single_runtime_takeover_keeps_legacy_process_and_pid_fields(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18509))
    listening = {"value": True}
    stopped: list[tuple[int, int]] = []

    monkeypatch.setattr(
        "services.status_resolver.is_port_listening",
        lambda _: listening["value"],
    )
    monkeypatch.setattr(
        "services.hub.is_port_listening",
        lambda _: listening["value"],
    )
    monkeypatch.setattr(
        "services.hub.inspect_listening_port",
        lambda port: {
            "port": port,
            "pid": 931,
            "process_name": "python.exe",
            "executable": None,
            "command_line": "python server.py",
            "started_at": None,
        },
    )

    def stop_external(port: int, pid: int) -> None:
        stopped.append((port, pid))
        listening["value"] = False

    monkeypatch.setattr("services.hub.stop_confirmed_port_process", stop_external)

    with client:
        preview = client.post(
            f"/api/services/{service.id}/takeover",
            json={"confirm": False},
        )
        confirmed = client.post(
            f"/api/services/{service.id}/takeover",
            json={"confirm": True, "pid": 931},
        )

    assert preview.json()["process"]["pid"] == 931
    assert preview.json()["processes"][0]["pid"] == 931
    assert confirmed.status_code == 200
    assert confirmed.json()["stopped_pid"] == 931
    assert stopped == [(18509, 931)]


def test_takeover_reports_port_not_released_before_start(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, fake, store, _ = make_client(tmp_path)
    service = store.create_service(payload(tmp_path, port=18510))
    monkeypatch.setattr("services.status_resolver.is_port_listening", lambda _: True)
    monkeypatch.setattr("services.hub.is_port_listening", lambda _: True)
    monkeypatch.setattr(
        "services.hub.inspect_listening_port",
        lambda port: {
            "port": port,
            "pid": 941,
            "process_name": "python.exe",
            "executable": None,
            "command_line": "python server.py",
            "started_at": None,
        },
    )
    monkeypatch.setattr("services.hub.stop_confirmed_port_process", lambda *_: None)

    with client:
        result = client.post(
            f"/api/services/{service.id}/takeover",
            json={"confirm": True, "pids": [941]},
        )

    assert result.status_code == 409
    assert result.json()["error"] == "port_conflict"
    assert not [item for item in fake.operations if item[0] == "start"]


def test_takeover_start_failure_is_recorded_after_ports_are_released(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, fake, store, _ = make_client(tmp_path)
    raw = payload(tmp_path, port=18511)
    raw.update(
        health_check_type="process",
        runtime_items=[
            {"id": "main", "port": 18511, "command": "npm run web"},
            {"id": "api", "port": 18512, "command": "npm run api"},
        ],
    )
    service = store.create_service(raw)
    api = process_id_for(service.id, "api")
    listening = {18511}
    original_start = fake.start_process

    monkeypatch.setattr(
        "services.status_resolver.is_port_listening",
        lambda port: port in listening,
    )
    monkeypatch.setattr("services.hub.is_port_listening", lambda port: port in listening)
    monkeypatch.setattr(
        "services.hub.inspect_listening_port",
        lambda port: {
            "port": port,
            "pid": 951,
            "process_name": "node.exe",
            "executable": None,
            "command_line": "node web.js",
            "started_at": None,
        },
    )

    def stop_external(port: int, _: int) -> None:
        listening.discard(port)

    async def fail_second_start(process_id: str) -> None:
        if process_id == api:
            raise ProcessComposeError("api launch failed")
        await original_start(process_id)

    monkeypatch.setattr("services.hub.stop_confirmed_port_process", stop_external)
    monkeypatch.setattr(fake, "start_process", fail_second_start)

    with client:
        result = client.post(
            f"/api/services/{service.id}/takeover",
            json={"confirm": True, "pids": [951]},
        )
        last_run = client.app.state.hub.run_history.get_last_run(service.id)

    assert result.status_code == 502
    assert result.json()["error"] == "process_compose_error"
    assert last_run is not None
    assert last_run["exit_type"] == "start_failed"
    assert last_run["last_error"] == "api launch failed"
