from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import httpx

from .config_generator import process_id_for
from .port_scanner import is_port_listening
from .service_store import ServiceDefinition


ERROR_STATUSES = {"error", "failed", "launch failed", "terminated", "skipped"}
STARTING_STATUSES = {"pending", "starting", "launching"}


def _age_seconds(raw_state: dict[str, Any]) -> float:
    age = raw_state.get("age", 0)
    if isinstance(age, bool) or not isinstance(age, (int, float)):
        return 0.0
    # Process Compose exposes process age in nanoseconds.
    return max(0.0, float(age) / 1_000_000_000)


class StatusResolver:
    def __init__(
        self,
        *,
        health_timeout_seconds: float = 1.2,
        startup_grace_seconds: float = 15.0,
    ) -> None:
        self.startup_grace_seconds = max(0.0, float(startup_grace_seconds))
        self._health = httpx.AsyncClient(
            timeout=httpx.Timeout(health_timeout_seconds),
            trust_env=False,
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self._health.aclose()

    @staticmethod
    def _without_acknowledged_exit(
        raw_state: dict[str, Any] | None,
        acknowledged_pid: int | None,
    ) -> dict[str, Any] | None:
        if not isinstance(raw_state, dict) or not isinstance(acknowledged_pid, int):
            return raw_state
        pid = raw_state.get("pid")
        if bool(raw_state.get("is_running", False)) or pid != acknowledged_pid:
            return raw_state
        normalized = dict(raw_state)
        normalized.update(
            status="Stopped",
            is_running=False,
            pid=0,
            exit_code=0,
            launch_error=None,
        )
        return normalized

    async def _http_health(
        self,
        url: str,
        expected_status: int,
    ) -> tuple[bool, str]:
        try:
            response = await self._health.get(url)
            if response.status_code == expected_status:
                return True, f"HTTP {response.status_code}，符合预期"
            return (
                False,
                f"HTTP 返回 {response.status_code}，预期 {expected_status}",
            )
        except httpx.HTTPError as exc:
            return False, f"HTTP 请求失败：{exc.__class__.__name__}"

    async def resolve_one(
        self,
        service: ServiceDefinition,
        raw_state: dict[str, Any] | None,
        *,
        controller_online: bool,
    ) -> dict[str, Any]:
        view = service.to_dict()
        desired_config = service.runtime_config()
        active_config = service.active_config
        view.update(
            process_id=process_id_for(service.id),
            state="Unknown",
            pid=None,
            started_at=None,
            uptime_seconds=None,
            error=None,
            controller_online=controller_online,
            pending_restart=active_config is not None,
            effective_port=desired_config.port,
            effective_url=desired_config.url,
            effective_health_url=desired_config.health_url,
            effective_health_check_type=desired_config.health_check_type,
            effective_health_expected_status=desired_config.health_expected_status,
            health_check={
                "type": desired_config.health_check_type,
                "status": "unknown",
                "target": (
                    desired_config.health_url
                    if desired_config.health_check_type == "http"
                    else (
                        f"127.0.0.1:{desired_config.port}"
                        if desired_config.health_check_type == "tcp"
                        else "Process Compose PID"
                    )
                ),
                "expected_status": (
                    desired_config.health_expected_status
                    if desired_config.health_check_type == "http"
                    else None
                ),
                "detail": "尚未检查",
            },
            port_occupant=None,
        )
        if not service.enabled:
            view.update(
                state="Disabled",
                pid=None,
                error=None,
                pending_restart=False,
            )
            return view
        if not controller_online:
            view["error"] = "Process Compose Controller Offline"
            return view

        raw_state = raw_state or {}
        running = bool(raw_state.get("is_running", False))
        raw_status = str(raw_state.get("status", "")).strip().lower()
        pid = raw_state.get("pid")
        view["pid"] = pid if isinstance(pid, int) and pid > 0 else None
        if running:
            effective = active_config or desired_config
            age_seconds = _age_seconds(raw_state)
            view.update(
                effective_port=effective.port,
                effective_url=effective.url,
                effective_health_url=effective.health_url,
                effective_health_check_type=effective.health_check_type,
                effective_health_expected_status=effective.health_expected_status,
                uptime_seconds=age_seconds,
                started_at=(
                    datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
                ).isoformat(),
            )
            health = {
                "type": effective.health_check_type,
                "status": "checking",
                "target": (
                    effective.health_url
                    if effective.health_check_type == "http"
                    else (
                        f"127.0.0.1:{effective.port}"
                        if effective.health_check_type == "tcp"
                        else f"PID {view['pid']}"
                    )
                ),
                "expected_status": (
                    effective.health_expected_status
                    if effective.health_check_type == "http"
                    else None
                ),
                "detail": "正在检查",
            }
            view["health_check"] = health
            if effective.health_check_type == "process":
                view["state"] = "Managed Running"
                health.update(status="passing", detail="Process Compose 进程正在运行")
            elif effective.health_check_type == "http":
                if not effective.health_url:
                    ok, detail = False, "HTTP 检查缺少 Health URL"
                else:
                    ok, detail = await self._http_health(
                        effective.health_url,
                        effective.health_expected_status,
                    )
                health["detail"] = detail
                if ok:
                    view["state"] = "Healthy"
                    health["status"] = "passing"
                elif age_seconds < self.startup_grace_seconds:
                    view["state"] = "Starting"
                    health["status"] = "checking"
                else:
                    view["state"] = "Unhealthy"
                    view["error"] = detail
                    health["status"] = "failing"
            elif not is_port_listening(effective.port):
                detail = f"TCP 端口 {effective.port} 未监听"
                health["detail"] = detail
                if age_seconds < self.startup_grace_seconds:
                    view["state"] = "Starting"
                else:
                    view["state"] = "Unhealthy"
                    view["error"] = detail
                    health["status"] = "failing"
            else:
                view["state"] = "Managed Running"
                health.update(status="passing", detail=f"TCP 端口 {effective.port} 正在监听")
            return view

        if raw_status in STARTING_STATUSES:
            view["state"] = "Starting"
            view["health_check"].update(status="checking", detail="进程正在启动")
            return view

        # A PID on a non-running controller entry belongs to the previous run.
        # It is retained by RunHistoryStore, not exposed as a current PID.
        view["pid"] = None

        listening_port = None
        if active_config is not None and is_port_listening(active_config.port):
            listening_port = active_config.port
            view.update(
                effective_port=active_config.port,
                effective_url=active_config.url,
                effective_health_url=active_config.health_url,
            )
        elif is_port_listening(service.port):
            listening_port = service.port
        if listening_port is not None:
            view["state"] = "External Running"
            health = view["health_check"]
            if health["type"] == "tcp":
                health.update(status="passing", detail=f"TCP 端口 {listening_port} 正在监听")
            else:
                health.update(status="unknown", detail="检测到外部实例，未使用管理器 PID 判定")
            return view

        exit_code = raw_state.get("exit_code")
        if raw_status in ERROR_STATUSES or (
            isinstance(exit_code, int) and exit_code != 0 and raw_status not in {"disabled", "stopped"}
        ):
            view["state"] = "Error"
            launch_error = raw_state.get("launch_error")
            view["error"] = str(
                launch_error
                or f"Process Compose 状态：{raw_state.get('status', 'Error')}（Exit Code {exit_code}）"
            )
        else:
            view["state"] = "Stopped"
        view["health_check"].update(status="inactive", detail="服务当前未运行")
        return view

    async def _resolve_additional_runtime(
        self,
        service: ServiceDefinition,
        item: Any,
        raw_state: dict[str, Any] | None,
        *,
        controller_online: bool,
    ) -> dict[str, Any]:
        process_id = process_id_for(service.id, item.id)
        view: dict[str, Any] = {
            "id": item.id,
            "port": item.port,
            "command": item.command,
            "process_id": process_id,
            "state": "Unknown",
            "pid": None,
            "error": None,
            "health_check": {
                "type": "tcp",
                "status": "unknown",
                "target": f"127.0.0.1:{item.port}",
                "expected_status": None,
                "detail": "尚未检查",
            },
        }
        if not service.enabled:
            view["state"] = "Disabled"
            view["health_check"].update(status="inactive", detail="服务已禁用")
            return view
        if not controller_online:
            view["error"] = "Process Compose Controller Offline"
            return view

        raw_state = raw_state or {}
        running = bool(raw_state.get("is_running", False))
        raw_status = str(raw_state.get("status", "")).strip().lower()
        pid = raw_state.get("pid")
        view["pid"] = pid if isinstance(pid, int) and pid > 0 else None
        if running:
            age_seconds = _age_seconds(raw_state)
            if is_port_listening(item.port):
                view["state"] = "Managed Running"
                view["health_check"].update(
                    status="passing",
                    detail=f"TCP 端口 {item.port} 正在监听",
                )
            elif age_seconds < self.startup_grace_seconds:
                view["state"] = "Starting"
                view["health_check"].update(
                    status="checking",
                    detail=f"正在等待 TCP 端口 {item.port}",
                )
            else:
                detail = f"TCP 端口 {item.port} 未监听"
                view.update(state="Unhealthy", error=detail)
                view["health_check"].update(status="failing", detail=detail)
            return view
        if raw_status in STARTING_STATUSES:
            view["state"] = "Starting"
            view["health_check"].update(status="checking", detail="进程正在启动")
            return view
        view["pid"] = None
        if is_port_listening(item.port):
            view["state"] = "External Running"
            view["health_check"].update(
                status="passing",
                detail=f"TCP 端口 {item.port} 由外部进程监听",
            )
            return view
        exit_code = raw_state.get("exit_code")
        if raw_status in ERROR_STATUSES or (
            isinstance(exit_code, int)
            and exit_code != 0
            and raw_status not in {"disabled", "stopped"}
        ):
            view["state"] = "Error"
            view["error"] = str(
                raw_state.get("launch_error")
                or f"Process Compose 状态：{raw_state.get('status', 'Error')}（Exit Code {exit_code}）"
            )
            view["health_check"].update(status="failing", detail=view["error"])
        else:
            view["state"] = "Stopped"
            view["health_check"].update(status="inactive", detail="运行项当前未运行")
        return view

    async def resolve_service(
        self,
        service: ServiceDefinition,
        raw_states: dict[str, dict[str, Any]],
        *,
        controller_online: bool,
        acknowledged_exits: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        acknowledged_exits = acknowledged_exits or {}
        primary_process_id = process_id_for(service.id)
        primary = await self.resolve_one(
            service,
            self._without_acknowledged_exit(
                raw_states.get(primary_process_id),
                acknowledged_exits.get(primary_process_id),
            ),
            controller_online=controller_online,
        )
        active_items = service.active_config.items() if service.active_config else ()
        use_active = bool(active_items) and any(
            bool((raw_states.get(process_id_for(service.id, item.id)) or {}).get("is_running"))
            or is_port_listening(item.port)
            for item in active_items
        )
        effective_items = active_items if use_active else service.items()
        runtime_views: list[dict[str, Any]] = [
            {
                "id": effective_items[0].id,
                "port": int(primary.get("effective_port") or effective_items[0].port),
                "command": effective_items[0].command,
                "process_id": process_id_for(service.id),
                "state": primary["state"],
                "pid": primary.get("pid"),
                "error": primary.get("error"),
                "health_check": primary.get("health_check"),
            }
        ]
        if len(effective_items) > 1:
            runtime_views.extend(
                await asyncio.gather(
                    *(
                        self._resolve_additional_runtime(
                            service,
                            item,
                            self._without_acknowledged_exit(
                                raw_states.get(process_id_for(service.id, item.id)),
                                acknowledged_exits.get(process_id_for(service.id, item.id)),
                            ),
                            controller_online=controller_online,
                        )
                        for item in effective_items[1:]
                    )
                )
            )
        primary["runtime_views"] = runtime_views
        primary["process_ids"] = [item["process_id"] for item in runtime_views]
        primary["pids"] = [item["pid"] for item in runtime_views if item.get("pid")]
        primary["effective_runtime_items"] = [
            {"id": item.id, "port": item.port, "command": item.command}
            for item in effective_items
        ]
        if len(runtime_views) == 1:
            return primary

        states = [str(item["state"]) for item in runtime_views]
        passing = sum(state in {"Healthy", "Managed Running", "External Running"} for state in states)
        external = sum(state == "External Running" for state in states)
        errors = [
            f":{item['port']} {item['error']}"
            for item in runtime_views
            if item.get("error")
        ]
        if "Disabled" in states:
            overall = "Disabled"
        elif "Unknown" in states:
            overall = "Unknown"
        elif external and external != len(states):
            overall = "Mixed Running"
            errors.append("运行项来源不一致，需要统一纳入管理")
        elif "Error" in states:
            overall = "Unhealthy" if passing or "Starting" in states else "Error"
        elif "Unhealthy" in states:
            overall = "Unhealthy"
        elif "Starting" in states:
            overall = "Starting"
        elif all(state == "External Running" for state in states):
            overall = "External Running"
        elif all(state in {"Healthy", "Managed Running", "External Running"} for state in states):
            overall = "Healthy" if states[0] == "Healthy" else "Managed Running"
        elif all(state == "Stopped" for state in states):
            overall = "Stopped"
        else:
            overall = "Unhealthy"
            errors.append(f"仅 {passing}/{len(runtime_views)} 个运行项正常")

        if overall in {"Healthy", "Managed Running", "External Running"}:
            health_status = "passing"
        elif overall == "Starting":
            health_status = "checking"
        elif overall in {"Error", "Unhealthy", "Mixed Running"}:
            health_status = "failing"
        elif overall in {"Stopped", "Disabled"}:
            health_status = "inactive"
        else:
            health_status = "unknown"
        primary["state"] = overall
        primary["error"] = "；".join(errors) or None
        primary["health_check"] = {
            "type": "multi",
            "status": health_status,
            "target": "、".join(f"127.0.0.1:{item['port']}" for item in runtime_views),
            "expected_status": None,
            "detail": f"{passing}/{len(runtime_views)} 个运行项端口正常",
        }
        return primary

    async def resolve_all(
        self,
        services: Iterable[ServiceDefinition],
        raw_states: dict[str, dict[str, Any]],
        *,
        controller_online: bool,
        acknowledged_exits_by_service: dict[str, dict[str, int]] | None = None,
    ) -> list[dict[str, Any]]:
        service_list = list(services)
        acknowledged_exits_by_service = acknowledged_exits_by_service or {}
        resolved = await asyncio.gather(
            *(
                self.resolve_service(
                    service,
                    raw_states,
                    controller_online=controller_online,
                    acknowledged_exits=acknowledged_exits_by_service.get(service.id),
                )
                for service in service_list
            )
        )
        priority = {
            "Healthy": 0,
            "Managed Running": 0,
            "Starting": 0,
            "External Running": 1,
            "Mixed Running": 2,
            "Unhealthy": 2,
            "Error": 2,
            "Stopped": 3,
            "Disabled": 4,
            "Unknown": 5,
        }
        order = {service.id: index for index, service in enumerate(service_list)}
        return sorted(
            resolved,
            key=lambda item: (priority.get(str(item["state"]), 5), order[item["id"]]),
        )
