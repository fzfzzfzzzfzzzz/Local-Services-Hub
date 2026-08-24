from __future__ import annotations

from collections import Counter
import asyncio
from dataclasses import replace
import os
from pathlib import Path
from typing import Any

from .config_generator import ProcessComposeConfigGenerator, process_id_for
from .port_scanner import is_port_listening, recommended_ports
from .process_compose import (
    ControllerAuthenticationError,
    ControllerOffline,
    ProcessComposeClient,
    ProcessComposeError,
)
from .process_inspector import (
    ProcessInspectionError,
    inspect_listening_port,
    stop_confirmed_port_process,
)
from .runtime_store import LogArchiveStore, RunHistoryStore, classify_logs
from .service_group_store import ServiceGroup, ServiceGroupStore
from .service_store import (
    ActiveConfiguration,
    ServiceDefinition,
    ServiceStore,
    ServiceStoreError,
)
from .status_resolver import StatusResolver


MANAGED_STATES = {"Managed Running", "Healthy", "Starting", "Unhealthy"}


def _matches_active_config(
    service: ServiceDefinition,
    active: ActiveConfiguration,
) -> bool:
    return service.working_dir == active.working_dir and service.items() == active.items()


def _open_directory(path: str) -> None:
    if not hasattr(os, "startfile"):
        raise OSError("当前操作系统不支持打开目录")
    os.startfile(path)  # type: ignore[attr-defined]


class HubConflict(RuntimeError):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.context = context or {}


class HubService:
    def __init__(
        self,
        store: ServiceStore,
        process_compose: ProcessComposeClient,
        generator: ProcessComposeConfigGenerator,
        status_resolver: StatusResolver,
        group_store: ServiceGroupStore,
        run_history: RunHistoryStore,
        log_archive: LogArchiveStore,
    ) -> None:
        self.store = store
        self.process_compose = process_compose
        self.generator = generator
        self.status_resolver = status_resolver
        self.group_store = group_store
        self.run_history = run_history
        self.log_archive = log_archive
        self._mutation_lock = asyncio.Lock()
        self._config_sync_warning: str | None = None
        self._stop_timeout_seconds = 8.0
        self._hub_shutdown_delay_seconds = 0.6
        self._dependency_timeout_seconds = 25.0
        self._takeover_previews: dict[
            str,
            tuple[tuple[str, int, str, int | None], ...],
        ] = {}

    async def close(self) -> None:
        await self.status_resolver.close()

    def _registered_port_owner(
        self,
        port: int,
        *,
        current_service: ServiceDefinition,
    ) -> dict[str, str] | None:
        candidates = self.store.list_services()
        for candidate in candidates:
            if candidate.id == current_service.id:
                continue
            ports = {item.port for item in candidate.items()}
            if candidate.active_config is not None:
                ports.update(item.port for item in candidate.active_config.items())
            if port in ports:
                return {
                    "id": candidate.id,
                    "name": candidate.name,
                    "relationship": "other_registered_service",
                }
        return {
            "id": current_service.id,
            "name": current_service.name,
            "relationship": "current_service_external_instance",
        }

    def _port_conflict(
        self,
        service: ServiceDefinition,
        inspected: dict[str, Any] | None,
        *,
        port: int | None = None,
    ) -> dict[str, Any]:
        process = inspected or {}
        target_port = port or service.port
        return {
            "port": target_port,
            "pid": process.get("pid"),
            "process_name": process.get("process_name"),
            "executable": process.get("executable"),
            "command_line": process.get("command_line"),
            "started_at": process.get("started_at"),
            "registered_service": self._registered_port_owner(
                target_port,
                current_service=service,
            ),
            "can_start": False,
        }

    async def prepare_hub_shutdown(self) -> None:
        """Fail before responding if the controller cannot stop this process."""
        await self.process_compose.is_online()

    async def prepare_hub_restart(self) -> None:
        """Fail before responding if the controller cannot restart this process."""
        await self.process_compose.is_online()

    async def shutdown_hub(self) -> None:
        """Run after the HTTP response so the browser receives confirmation first."""
        await asyncio.sleep(self._hub_shutdown_delay_seconds)
        await self.process_compose.stop_process("service_hub")

    async def restart_hub(self) -> None:
        """Run after the HTTP response so the browser can wait for the new instance."""
        await asyncio.sleep(self._hub_shutdown_delay_seconds)
        await self.process_compose.restart_process("service_hub")

    async def _controller_and_states(
        self,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        controller: dict[str, Any] = {
            "online": True,
            "state": "online",
            "url": self.process_compose.base_url,
            "error": None,
        }
        try:
            raw_states = await self.process_compose.list_processes()
        except ControllerAuthenticationError as exc:
            controller.update(online=False, state="authentication_error", error=str(exc))
            raw_states = {}
        except ControllerOffline as exc:
            controller.update(online=False, state="offline", error=str(exc))
            raw_states = {}
        except ProcessComposeError as exc:
            controller.update(online=False, state="error", error=str(exc))
            raw_states = {}
        return controller, raw_states

    @staticmethod
    def _all_runtime_items(service: ServiceDefinition) -> list[Any]:
        items = list(service.active_config.items()) if service.active_config else []
        known = {item.id for item in items}
        items.extend(item for item in service.items() if item.id not in known)
        return items

    async def _combined_logs(
        self,
        service: ServiceDefinition,
        *,
        limit: int,
    ) -> list[str]:
        items = self._all_runtime_items(service)
        results = await asyncio.gather(
            *(
                self.process_compose.get_logs(
                    process_id_for(service.id, item.id),
                    limit=limit,
                )
                for item in items
            ),
            return_exceptions=True,
        )
        lines: list[str] = []
        errors: list[BaseException] = []
        for item, result in zip(items, results, strict=True):
            if isinstance(result, BaseException):
                errors.append(result)
                continue
            prefix = f"[:{item.port}] " if len(items) > 1 else ""
            lines.extend(f"{prefix}{line}" for line in result)
        if not lines and errors:
            raise errors[0]
        return lines

    async def snapshot(self) -> dict[str, Any]:
        services = self.store.list_services()
        controller, raw_states = await self._controller_and_states()
        acknowledged_by_service: dict[str, dict[str, int]] = {}
        for service in services:
            primary_process_id = process_id_for(service.id)
            primary_state = raw_states.get(primary_process_id) or {}
            last_run = self.run_history.get_last_run(service.id)
            expected_exit = self.run_history.get_expected_exit(service.id)
            # Incrementally migrate clean-stop history created before
            # acknowledged_exits existed. Only the exact residual PID is safe.
            if (
                (
                    isinstance(last_run, dict)
                    and last_run.get("exit_type") == "normal_stop"
                    and isinstance(last_run.get("pid"), int)
                    and last_run.get("pid") == primary_state.get("pid")
                    or expected_exit in {"stop", "restart"}
                )
                and isinstance(primary_state.get("pid"), int)
                and int(primary_state["pid"]) > 0
                and not bool(primary_state.get("is_running", False))
            ):
                self.run_history.acknowledge_exits(
                    service.id,
                    {primary_process_id: int(primary_state["pid"])},
                )
            acknowledged_by_service[service.id] = (
                self.run_history.reconcile_acknowledged_exits(
                    service.id,
                    raw_states,
                )
            )
        views = await self.status_resolver.resolve_all(
            services,
            raw_states,
            controller_online=bool(controller["online"]),
            acknowledged_exits_by_service=acknowledged_by_service,
        )
        services_by_id = {service.id: service for service in services}
        external_runtimes = [
            (view, runtime)
            for view in views
            for runtime in view.get("runtime_views", [])
            if runtime.get("state") == "External Running"
        ]
        if external_runtimes:
            inspected = await asyncio.gather(
                *(
                    asyncio.to_thread(
                        inspect_listening_port,
                        int(runtime["port"]),
                    )
                    for _, runtime in external_runtimes
                ),
                return_exceptions=True,
            )
            for (view, runtime), process in zip(external_runtimes, inspected, strict=True):
                if isinstance(process, dict):
                    runtime["pid"] = process.get("pid")
                    runtime["process"] = process
                    view.setdefault("external_processes", []).append(process)
                    service = services_by_id[str(view["id"])]
                    conflict = self._port_conflict(
                        service,
                        process,
                        port=int(runtime["port"]),
                    )
                    if view.get("port_occupant") is None:
                        view["port_occupant"] = conflict
                    if len(view.get("runtime_views", [])) == 1:
                        view["pid"] = process.get("pid")
                        view["started_at"] = process.get("started_at")
        for view in views:
            view["pids"] = list(
                dict.fromkeys(
                    int(runtime["pid"])
                    for runtime in view.get("runtime_views", [])
                    if isinstance(runtime.get("pid"), int) and runtime["pid"] > 0
                )
            )
        issue_views = [item for item in views if item["state"] in {"Error", "Unhealthy"}]
        issue_logs: list[list[str] | Exception] = []
        if controller["online"] and issue_views:
            issue_logs = list(
                await asyncio.gather(
                    *(
                        self._combined_logs(
                            next(
                                service
                                for service in services
                                if service.id == str(item["id"])
                            ),
                            limit=80,
                        )
                        for item in issue_views
                    ),
                    return_exceptions=True,
                )
            )
        diagnostics: dict[str, dict[str, Any]] = {}
        for view, lines in zip(issue_views, issue_logs):
            if isinstance(lines, list):
                diagnostics[str(view["id"])] = classify_logs(lines)

        views_by_id = {str(view["id"]): view for view in views}
        for view in views:
            service_id = str(view["id"])
            diagnosis = diagnostics.get(service_id, {})
            current_fault = view["state"] in {"Error", "Unhealthy", "Unknown"}
            last_error = (
                diagnosis.get("last_error") or view.get("error")
                if current_fault
                else None
            )
            view["last_error"] = last_error
            primary_process_id = process_id_for(service_id)
            raw_state = raw_states.get(primary_process_id) or {}
            view["last_run"] = await asyncio.to_thread(
                self.run_history.observe,
                service_id,
                raw_state,
                process_id=primary_process_id,
                last_error=last_error,
            )
            service = services_by_id[service_id]
            view["dependency_services"] = [
                {
                    "id": dependency_id,
                    "name": services_by_id[dependency_id].name,
                    "state": views_by_id.get(dependency_id, {}).get("state", "Unknown"),
                }
                for dependency_id in service.dependencies
                if dependency_id in services_by_id
            ]
        counts = Counter(str(item["state"]) for item in views)
        return {
            "controller": controller,
            "configuration": {
                "sync_pending": self._config_sync_warning is not None,
                "sync_error": self._config_sync_warning,
            },
            "store": {
                "error": self.store.degraded_error,
                "using_backup": self.store.using_backup,
            },
            "summary": {
                "running": counts["Healthy"] + counts["Managed Running"],
                "starting": counts["Starting"],
                "external": counts["External Running"],
                "mixed": counts["Mixed Running"],
                "issues": (
                    counts["Unhealthy"]
                    + counts["Error"]
                    + counts["Unknown"]
                    + counts["Mixed Running"]
                ),
                "stopped": counts["Stopped"],
                "disabled": counts["Disabled"],
                "unknown": counts["Unknown"],
                "total": len(views),
            },
            "services": views,
            "groups": [group.to_dict() for group in self.group_store.list_groups()],
        }

    async def get_service_view(self, service_id: str) -> dict[str, Any]:
        self.store.get_service(service_id)
        snapshot = await self.snapshot()
        return next(item for item in snapshot["services"] if item["id"] == service_id)

    async def recommended_ports(self) -> list[int]:
        services = self.store.list_services()
        return await asyncio.to_thread(
            recommended_ports,
            [item.port for service in services for item in service.items()],
        )

    def _dependency_order(self, service_ids: list[str]) -> list[ServiceDefinition]:
        services = {service.id: service for service in self.store.list_services()}
        order: list[ServiceDefinition] = []
        visited: set[str] = set()

        def visit(service_id: str) -> None:
            if service_id in visited:
                return
            service = services.get(service_id)
            if service is None:
                raise HubConflict(
                    "missing_dependency",
                    f"启动计划引用了不存在的服务：{service_id}",
                )
            for dependency_id in service.dependencies:
                visit(dependency_id)
            visited.add(service_id)
            order.append(service)

        for service_id in service_ids:
            visit(service_id)
        return order

    @staticmethod
    def _group_payload(group: ServiceGroup) -> dict[str, Any]:
        return group.to_dict()

    async def list_groups(self) -> dict[str, Any]:
        services = {service.id: service for service in self.store.list_services()}
        groups = []
        for group in self.group_store.list_groups():
            payload = self._group_payload(group)
            payload["service_details"] = [
                {"id": item, "name": services[item].name}
                for item in group.services
                if item in services
            ]
            groups.append(payload)
        return {"groups": groups}

    async def create_group(self, raw: dict[str, Any]) -> dict[str, Any]:
        async with self._mutation_lock:
            service_ids = {service.id for service in self.store.list_services()}
            group = self.group_store.create_group(raw, service_ids=service_ids)
        return {"group": self._group_payload(group)}

    async def update_group(self, group_id: str, raw: dict[str, Any]) -> dict[str, Any]:
        async with self._mutation_lock:
            service_ids = {service.id for service in self.store.list_services()}
            group = self.group_store.update_group(
                group_id,
                raw,
                service_ids=service_ids,
            )
        return {"group": self._group_payload(group)}

    async def delete_group(self, group_id: str) -> dict[str, Any]:
        async with self._mutation_lock:
            group = self.group_store.delete_group(group_id)
        return {"deleted": self._group_payload(group)}

    async def _reload_controller(self) -> str | None:
        try:
            await self.process_compose.reload_configuration()
            self._config_sync_warning = None
            return None
        except ProcessComposeError as exc:
            # The JSON and generated YAML are already committed as one bundle. The
            # controller can load the durable generated file when it comes back.
            self._config_sync_warning = str(exc)
            return self._config_sync_warning

    async def _commit_candidate(
        self,
        candidate: list[ServiceDefinition],
    ) -> str | None:
        """Validate both files first, then commit or roll back the pair together."""
        previous = self.store.list_services()
        self.store.prepare_services(candidate)
        try:
            self.generator.prepare(candidate)
        except Exception:
            self.store.discard_prepared()
            raise

        try:
            self.store.commit_prepared(candidate)
            self.generator.commit_prepared()
            # Moving the registry rollback file to services.json.bak is the commit
            # point. Until then both installed files can be restored without writes.
            self.store.finalize_prepared()
            self.generator.finalize_prepared()
        except Exception as exc:
            rollback_errors: list[str] = []
            try:
                self.generator.rollback_prepared()
            except ServiceStoreError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
            try:
                self.store.rollback_prepared(previous)
            except ServiceStoreError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
            if rollback_errors:
                raise ServiceStoreError(
                    f"配置事务失败且回滚不完整：{exc}；{'；'.join(rollback_errors)}"
                ) from exc
            if isinstance(exc, ServiceStoreError):
                raise
            raise ServiceStoreError(f"配置事务提交失败：{exc}") from exc

        return await self._reload_controller()

    @staticmethod
    def _assert_enabled(service: ServiceDefinition) -> None:
        if not service.enabled:
            raise HubConflict("service_disabled", "服务已禁用；请先在编辑页重新启用")

    async def _stop_and_confirm(self, service: ServiceDefinition) -> None:
        runtime_items = (
            service.active_config.items()
            if service.active_config is not None
            else service.items()
        )
        process_ids = [process_id_for(service.id, item.id) for item in runtime_items]
        initial_states = await self.process_compose.list_processes()
        observed_pids = {
            process_id: int(raw_state["pid"])
            for process_id in process_ids
            if isinstance((raw_state := initial_states.get(process_id) or {}).get("pid"), int)
            and int(raw_state["pid"]) > 0
        }
        await asyncio.to_thread(
            self.run_history.mark_expected_exit,
            service.id,
            "stop",
        )
        try:
            lines = await self._combined_logs(service, limit=500)
            await asyncio.to_thread(self.log_archive.save_latest, service.id, lines)
        except ProcessComposeError:
            pass
        for process_id in reversed(process_ids):
            await self.process_compose.stop_process(process_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._stop_timeout_seconds
        last_status = "unknown"
        listening_ports: list[int] = []
        while True:
            raw_states = await self.process_compose.list_processes()
            states = [raw_states.get(process_id) or {} for process_id in process_ids]
            last_status = "、".join(
                str(raw_state.get("status", "missing")) for raw_state in states
            )
            process_running = any(
                bool(raw_state.get("is_running", False))
                or str(raw_state.get("status", "")).strip().lower()
                in {"running", "pending", "starting", "launching"}
                for raw_state in states
            )
            port_checks = await asyncio.gather(
                *(asyncio.to_thread(is_port_listening, item.port) for item in runtime_items)
            )
            listening_ports = [
                item.port
                for item, listening in zip(runtime_items, port_checks, strict=True)
                if listening
            ]
            if not process_running and not listening_ports:
                observed_pids.update(
                    {
                        process_id: int(state["pid"])
                        for process_id, state in zip(process_ids, states, strict=True)
                        if isinstance(state.get("pid"), int) and int(state["pid"]) > 0
                    }
                )
                await asyncio.to_thread(
                    self.run_history.observe,
                    service.id,
                    states[0] if states else {},
                    process_id=process_ids[0] if process_ids else service.id,
                    last_error=None,
                )
                await asyncio.to_thread(
                    self.run_history.acknowledge_exits,
                    service.id,
                    observed_pids,
                )
                await asyncio.to_thread(
                    self.run_history.confirm_normal_stop,
                    service.id,
                    next(iter(observed_pids.values()), None),
                )
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.2, remaining))
        raise HubConflict(
            "stop_confirmation_timeout",
            f"未确认服务已完全停止（控制器状态：{last_status}，"
            f"仍监听端口：{', '.join(map(str, listening_ports)) or '无'}）；已保留登记和日志",
        )

    async def create_service(self, raw: dict[str, Any]) -> dict[str, Any]:
        async with self._mutation_lock:
            candidate, service = self.store.candidate_create(raw)
            warning = await self._commit_candidate(candidate)
        return {
            "service": await self.get_service_view(service.id),
            "config_sync_warning": warning,
        }

    async def update_service(
        self,
        service_id: str,
        raw: dict[str, Any],
        *,
        restart: bool | None,
    ) -> dict[str, Any]:
        async with self._mutation_lock:
            before = self.store.get_service(service_id)
            before_view = await self.get_service_view(service_id)
            _, preview = self.store.candidate_update(
                service_id,
                raw,
                active_config=before.active_config,
            )
            changed_critical = []
            if preview.working_dir != before.working_dir:
                changed_critical.append("working_dir")
            if preview.items() != before.items():
                changed_critical.append("runtime_items")
            was_managed = before_view["state"] in MANAGED_STATES
            disabling = before.enabled and raw.get("enabled") is False
            if before_view["state"] == "Mixed Running" and (
                disabling or changed_critical
            ):
                raise HubConflict(
                    "mixed_running_requires_takeover",
                    "服务存在混合运行项；请先统一纳入管理再修改运行配置",
                    context={"state": before_view["state"], "can_takeover": True},
                )
            if disabling and not before_view["controller_online"]:
                raise ControllerOffline("控制器离线，无法确认服务已停止，因此不能禁用")
            if disabling and before_view["state"] == "External Running":
                raise HubConflict(
                    "external_running_cannot_disable",
                    "端口仍由外部进程使用；请先停止外部进程再禁用服务",
                )
            if disabling and was_managed and restart is not True:
                raise HubConflict(
                    "stop_decision_required",
                    "运行中的服务只能在确认停止后禁用",
                )
            if was_managed and changed_critical and not disabling and restart is None:
                raise HubConflict(
                    "restart_decision_required",
                    "运行中的服务修改了端口、目录或命令；请选择暂不重启或保存并重启",
                )
            if disabling and was_managed:
                await self._stop_and_confirm(before)

            active_config = before.active_config
            if disabling:
                active_config = None
            elif was_managed and changed_critical and active_config is None:
                active_config = before.runtime_config()
            candidate, service = self.store.candidate_update(
                service_id,
                raw,
                active_config=active_config,
            )
            if service.active_config and _matches_active_config(
                service,
                service.active_config,
            ):
                service = replace(service, active_config=None)
                candidate = [
                    item if item.id != service.id else service
                    for item in candidate
                ]
            warning = await self._commit_candidate(candidate)
            if restart is True and was_managed and changed_critical and not disabling:
                if warning:
                    raise ControllerOffline(
                        f"配置已保存，但控制器离线，无法重启：{warning}"
                    )
                await self._restart_confirmed(service)
                service = self.store.set_active_config(service.id, None)
        return {
            "service": await self.get_service_view(service.id),
            "config_sync_warning": warning,
            "restart_deferred": bool(service.active_config is not None),
            "disabled_after_stop": disabling and was_managed,
        }

    async def delete_service(self, service_id: str, *, stop: bool) -> dict[str, Any]:
        async with self._mutation_lock:
            service = self.store.get_service(service_id)
            view = await self.get_service_view(service_id)
            was_external = view["state"] == "External Running"
            if view["state"] == "Mixed Running":
                raise HubConflict(
                    "mixed_running_requires_takeover",
                    "服务存在混合运行项；请先统一纳入管理再删除",
                    context={"state": view["state"], "can_takeover": True},
                )
            if view["state"] in MANAGED_STATES and not stop:
                raise HubConflict(
                    "stop_required",
                    "服务正在由 Process Compose 管理；请确认“停止并删除”",
                )
            if view["state"] in MANAGED_STATES:
                await self._stop_and_confirm(service)
            candidate, deleted = self.store.candidate_delete(service_id)
            warning = await self._commit_candidate(candidate)
            affected_groups = await asyncio.to_thread(
                self.group_store.remove_service_references,
                service_id,
            )
            await asyncio.to_thread(self.run_history.remove_service, service_id)
            await asyncio.to_thread(self.log_archive.remove_service, service_id)
        return {
            "deleted": deleted.to_dict(),
            "external_process_continues": was_external,
            "config_sync_warning": warning,
            "affected_groups": affected_groups,
        }

    async def restore_backup(self) -> dict[str, Any]:
        async with self._mutation_lock:
            self.store.restore_backup()
            self.generator.generate(self.store.list_services())
            warning = await self._reload_controller()
        return {"restored": True, "config_sync_warning": warning}

    async def _archive_current_logs(self, service: ServiceDefinition) -> None:
        try:
            lines = await self._combined_logs(service, limit=500)
            await asyncio.to_thread(self.log_archive.rotate, service.id, lines)
            for item in self._all_runtime_items(service):
                await self.process_compose.clear_logs(
                    process_id_for(service.id, item.id)
                )
        except ProcessComposeError:
            # Log rotation must never prevent a service from starting.
            return

    async def _raise_port_conflict(
        self,
        service: ServiceDefinition,
        port: int | None = None,
    ) -> None:
        target_port = port or service.port
        inspected = await asyncio.to_thread(inspect_listening_port, target_port)
        conflict = self._port_conflict(service, inspected, port=target_port)
        process_bits = [
            str(conflict["process_name"] or "未知进程"),
            f"PID {conflict['pid']}" if conflict["pid"] else None,
        ]
        process_detail = "，".join(item for item in process_bits if item)
        detail = f"端口 {target_port} 已被 {process_detail} 占用；当前服务未启动"
        await asyncio.to_thread(
            self.run_history.record_start_failure,
            service.id,
            detail,
        )
        raise HubConflict(
            "port_conflict",
            detail,
            context={"port_conflict": conflict},
        )

    async def _restart_confirmed(self, service: ServiceDefinition) -> None:
        """Stop, verify the ports are actually free, then start fresh.

        Restarting straight through the controller binds the new process onto
        whatever listens on the port now: the old process may already be dead
        with an unrelated process holding the port, and the health check would
        be answered by that intruder. Splitting the restart into a confirmed
        stop, a port check, and a clean start surfaces the conflict instead.
        """
        await self._archive_current_logs(service)
        await self._stop_and_confirm(service)
        await self._ensure_runtime_ports_free(service)
        await self._reload_controller()
        await self._start_runtime_processes(service)

    async def _ensure_runtime_ports_free(self, service: ServiceDefinition) -> None:
        for item in service.items():
            if await asyncio.to_thread(is_port_listening, item.port):
                await self._raise_port_conflict(service, item.port)

    async def _start_runtime_processes(self, service: ServiceDefinition) -> None:
        started: list[str] = []
        try:
            for item in service.items():
                process_id = process_id_for(service.id, item.id)
                await self.process_compose.start_process(process_id)
                started.append(process_id)
        except ProcessComposeError as exc:
            for process_id in reversed(started):
                try:
                    await self.process_compose.stop_process(process_id)
                except ProcessComposeError:
                    pass
            await asyncio.to_thread(
                self.run_history.record_start_failure,
                service.id,
                str(exc),
            )
            raise

    async def _wait_until_ready(
        self,
        service: ServiceDefinition,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._dependency_timeout_seconds
        last_view: dict[str, Any] | None = None
        while True:
            raw_states = await self.process_compose.list_processes()
            last_view = await self.status_resolver.resolve_service(
                self.store.get_service(service.id),
                raw_states,
                controller_online=True,
            )
            if last_view["state"] in {
                "Healthy",
                "Managed Running",
                "External Running",
            }:
                return last_view
            if last_view["state"] in {"Unhealthy", "Error"}:
                detail = str(last_view.get("error") or "健康检查未通过")
                await asyncio.to_thread(
                    self.run_history.record_start_failure,
                    service.id,
                    detail,
                )
                raise HubConflict(
                    "dependency_start_failed",
                    f"服务“{service.name}”启动失败：{detail}",
                    context={"failed_service_id": service.id},
                )
            remaining = deadline - loop.time()
            if remaining <= 0:
                detail = f"等待“{service.name}”健康检查超时"
                await asyncio.to_thread(
                    self.run_history.record_start_failure,
                    service.id,
                    detail,
                )
                raise HubConflict(
                    "dependency_start_timeout",
                    detail,
                    context={"failed_service_id": service.id},
                )
            await asyncio.sleep(min(0.4, remaining))

    async def _start_plan(
        self,
        service_ids: list[str],
        *,
        allow_external_roots: bool,
    ) -> list[dict[str, Any]]:
        order = self._dependency_order(service_ids)
        root_ids = set(service_ids)
        await self.process_compose.reload_configuration()
        self._config_sync_warning = None
        results: list[dict[str, Any]] = []
        for service in order:
            self._assert_enabled(service)
            if not Path(service.working_dir).is_dir():
                raise HubConflict(
                    "missing_directory",
                    f"服务“{service.name}”的项目目录不存在：{service.working_dir}",
                    context={"failed_service_id": service.id},
                )
            view = await self.get_service_view(service.id)
            state = str(view["state"])
            if state in {"Healthy", "Managed Running"}:
                results.append({"id": service.id, "name": service.name, "status": "already_running"})
                continue
            if state == "External Running":
                if service.id not in root_ids or allow_external_roots:
                    results.append({"id": service.id, "name": service.name, "status": "external_running"})
                    continue
                await self._raise_port_conflict(service)
            if state == "Mixed Running":
                raise HubConflict(
                    "mixed_running_requires_takeover",
                    f"服务“{service.name}”存在混合运行项；请先统一纳入管理",
                    context={"failed_service_id": service.id, "can_takeover": True},
                )
            if state == "Starting":
                await self._wait_until_ready(service)
                results.append({"id": service.id, "name": service.name, "status": "running"})
                continue
            if state == "Unhealthy":
                raise HubConflict(
                    "dependency_unhealthy",
                    f"服务“{service.name}”正在运行，但健康检查失败：{view.get('error') or '未知原因'}",
                    context={"failed_service_id": service.id},
                )
            await self._ensure_runtime_ports_free(service)
            await self._archive_current_logs(service)
            await self._start_runtime_processes(service)
            if service.active_config is not None:
                self.store.set_active_config(service.id, None)
            await self._wait_until_ready(service)
            results.append({"id": service.id, "name": service.name, "status": "running"})
        return results

    async def start_service(self, service_id: str) -> dict[str, Any]:
        service = self.store.get_service(service_id)
        self._assert_enabled(service)
        if not Path(service.working_dir).is_dir():
            raise HubConflict("missing_directory", f"项目目录不存在：{service.working_dir}")
        if any(not item.command.strip() for item in service.items()):
            raise HubConflict("empty_command", "启动命令不能为空")
        view = await self.get_service_view(service_id)
        if not view["controller_online"]:
            raise ControllerOffline("Process Compose Controller Offline")
        if view["state"] in MANAGED_STATES:
            raise HubConflict("already_running", "服务已经由 Process Compose 运行")
        if view["state"] == "Mixed Running":
            raise HubConflict(
                "mixed_running_requires_takeover",
                "服务存在混合运行项；请先统一纳入管理",
                context={"state": view["state"], "can_takeover": True},
            )
        if service.dependencies:
            async with self._mutation_lock:
                results = await self._start_plan(
                    [service_id],
                    allow_external_roots=False,
                )
            return {
                "service_id": service_id,
                "operation": "start",
                "plan": results,
            }
        await self._ensure_runtime_ports_free(service)
        await self._archive_current_logs(service)
        await self.process_compose.reload_configuration()
        self._config_sync_warning = None
        await self._start_runtime_processes(service)
        if service.active_config is not None:
            self.store.set_active_config(service_id, None)
        return {"service_id": service_id, "operation": "start", "plan": []}

    async def start_group(self, group_id: str) -> dict[str, Any]:
        group = self.group_store.get_group(group_id)
        async with self._mutation_lock:
            results = await self._start_plan(
                list(group.services),
                allow_external_roots=True,
            )
        return {
            "group": self._group_payload(group),
            "operation": "start_group",
            "plan": results,
        }

    async def stop_service(self, service_id: str) -> dict[str, Any]:
        service = self.store.get_service(service_id)
        self._assert_enabled(service)
        view = await self.get_service_view(service_id)
        if not view["controller_online"]:
            raise ControllerOffline("Process Compose Controller Offline")
        if view["state"] not in MANAGED_STATES:
            raise HubConflict("not_managed", "只有 Managed Running 服务可以关闭")
        await self._stop_and_confirm(service)
        if self.store.get_service(service_id).active_config is not None:
            self.store.set_active_config(service_id, None)
        return {"service_id": service_id, "operation": "stop"}

    async def restart_service(self, service_id: str) -> dict[str, Any]:
        service = self.store.get_service(service_id)
        self._assert_enabled(service)
        view = await self.get_service_view(service_id)
        if not view["controller_online"]:
            raise ControllerOffline("Process Compose Controller Offline")
        if view["state"] in {"External Running", "Mixed Running"}:
            detail = (
                "服务已由外部程序重启，请重新纳入管理"
                if view["state"] == "External Running"
                else "服务包含外部运行项，请统一纳入管理"
            )
            raise HubConflict(
                "external_running",
                detail,
                context={
                    "state": view["state"],
                    "can_takeover": True,
                },
            )
        if view["state"] not in MANAGED_STATES:
            raise HubConflict(
                "not_managed",
                f"当前状态为 {view['state']}，无法重启",
                context={"state": view["state"], "can_takeover": False},
            )
        await self._restart_confirmed(service)
        if self.store.get_service(service_id).active_config is not None:
            self.store.set_active_config(service_id, None)
        return {"service_id": service_id, "operation": "restart"}

    async def _takeover_runtime_snapshot(
        self,
        service: ServiceDefinition,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        controller, raw_states = await self._controller_and_states()
        if not controller["online"]:
            raise ControllerOffline("Process Compose Controller Offline")
        acknowledged = self.run_history.reconcile_acknowledged_exits(
            service.id,
            raw_states,
        )
        view = await self.status_resolver.resolve_service(
            service,
            raw_states,
            controller_online=True,
            acknowledged_exits=acknowledged,
        )
        if view["state"] not in {"External Running", "Mixed Running"}:
            raise HubConflict(
                "not_external",
                "当前服务已不包含可纳管的外部运行项",
                context={"state": view["state"]},
            )

        processes: list[dict[str, Any]] = []
        external_records: list[dict[str, Any]] = []
        for runtime in view.get("runtime_views", []):
            process_id = str(runtime["process_id"])
            raw_state = raw_states.get(process_id) or {}
            runtime_state = str(runtime["state"])
            if runtime_state == "External Running":
                source = "external"
            elif bool(raw_state.get("is_running", False)) or runtime_state in MANAGED_STATES:
                source = "managed"
            else:
                source = "inactive"
            record = {
                "runtime_id": runtime["id"],
                "process_id": process_id,
                "port": int(runtime["port"]),
                "source": source,
                "state": runtime_state,
                "pid": (
                    int(raw_state["pid"])
                    if source == "managed"
                    and isinstance(raw_state.get("pid"), int)
                    and int(raw_state["pid"]) > 0
                    else None
                ),
                "process_name": "Process Compose" if source == "managed" else None,
                "executable": None,
                "command_line": runtime.get("command"),
                "started_at": None,
                "command": runtime.get("command"),
            }
            processes.append(record)
            if source == "managed" and record["pid"] is None:
                raise HubConflict(
                    "runtime_confirmation_stale",
                    f"运行项 {process_id} 缺少可确认的 PID，请刷新后重试",
                    context={"processes": processes},
                )
            if source == "external":
                external_records.append(record)

        inspections = await asyncio.gather(
            *(
                asyncio.to_thread(inspect_listening_port, int(record["port"]))
                for record in external_records
            )
        )
        for record, inspected in zip(external_records, inspections, strict=True):
            if not isinstance(inspected, dict):
                raise HubConflict(
                    "runtime_confirmation_stale",
                    f"端口 {record['port']} 的外部进程已发生变化，请重新预览",
                    context={"processes": processes},
                )
            record.update(
                pid=inspected.get("pid"),
                process_name=inspected.get("process_name"),
                executable=inspected.get("executable"),
                command_line=inspected.get("command_line") or record.get("command"),
                started_at=inspected.get("started_at"),
            )
            if not isinstance(record.get("pid"), int) or int(record["pid"]) <= 0:
                raise HubConflict(
                    "runtime_confirmation_stale",
                    f"端口 {record['port']} 缺少可确认的 PID，请刷新后重试",
                    context={"processes": processes},
                )
        return view, processes

    @staticmethod
    def _takeover_signature(
        processes: list[dict[str, Any]],
    ) -> tuple[tuple[str, int, str, int | None], ...]:
        return tuple(
            (
                str(item["process_id"]),
                int(item["port"]),
                str(item["source"]),
                int(item["pid"]) if isinstance(item.get("pid"), int) else None,
            )
            for item in processes
        )

    async def _stop_managed_takeover_items(
        self,
        service: ServiceDefinition,
        processes: list[dict[str, Any]],
    ) -> None:
        managed = [item for item in processes if item["source"] == "managed"]
        if not managed:
            return
        await asyncio.to_thread(
            self.run_history.mark_expected_exit,
            service.id,
            "restart",
        )
        for item in reversed(managed):
            await self.process_compose.stop_process(str(item["process_id"]))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._stop_timeout_seconds
        while True:
            raw_states = await self.process_compose.list_processes()
            running = [
                item
                for item in managed
                if bool((raw_states.get(str(item["process_id"])) or {}).get("is_running"))
                or str(
                    (raw_states.get(str(item["process_id"])) or {}).get("status", "")
                ).strip().lower()
                in {"running", "pending", "starting", "launching"}
            ]
            if not running:
                process_pids = {
                    str(item["process_id"]): int(item["pid"])
                    for item in managed
                    if isinstance(item.get("pid"), int) and int(item["pid"]) > 0
                }
                await asyncio.to_thread(
                    self.run_history.acknowledge_exits,
                    service.id,
                    process_pids,
                )
                await asyncio.to_thread(
                    self.run_history.confirm_normal_stop,
                    service.id,
                    next(iter(process_pids.values()), None),
                )
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise HubConflict(
                    "stop_confirmation_timeout",
                    "统一纳管时未能确认所有受管运行项已经停止",
                )
            await asyncio.sleep(min(0.2, remaining))

    async def takeover_service(
        self,
        service_id: str,
        *,
        confirm: bool,
        pid: int | None,
        pids: list[int] | None = None,
    ) -> dict[str, Any]:
        service = self.store.get_service(service_id)
        self._assert_enabled(service)
        if not confirm:
            _, processes = await self._takeover_runtime_snapshot(service)
            self._takeover_previews[service.id] = self._takeover_signature(processes)
            running_processes = [item for item in processes if item.get("pid")]
            preview = {
                "service": service.to_dict(),
                "processes": processes,
                # Preserve the original single-process response shape.
                "process": running_processes[0] if len(running_processes) == 1 else None,
                "message": "将逐项停止当前实例，然后全部改由 Process Compose 启动。",
            }
            return {"requires_confirmation": True, **preview}
        expected = {item for item in (pids or []) if isinstance(item, int) and item > 0}
        if isinstance(pid, int) and pid > 0:
            expected.add(pid)

        async with self._mutation_lock:
            service = self.store.get_service(service_id)
            try:
                _, fresh_processes = await self._takeover_runtime_snapshot(service)
            except HubConflict as exc:
                if exc.code != "not_external":
                    raise
                raise HubConflict(
                    "runtime_confirmation_stale",
                    "运行来源已发生变化；未停止任何进程，请重新确认",
                    context=exc.context,
                ) from exc
            actual = {
                int(item["pid"])
                for item in fresh_processes
                if isinstance(item.get("pid"), int) and int(item["pid"]) > 0
            }
            previous_signature = self._takeover_previews.get(service.id)
            fresh_signature = self._takeover_signature(fresh_processes)
            if expected != actual or (
                previous_signature is not None
                and previous_signature != fresh_signature
            ):
                raise HubConflict(
                    "runtime_confirmation_stale",
                    "运行项、端口或 PID 已发生变化；未停止任何进程，请重新确认",
                    context={"processes": fresh_processes},
                )

            await self._archive_current_logs(service)
            await self._stop_managed_takeover_items(service, fresh_processes)
            external_by_pid: dict[int, dict[str, Any]] = {}
            for item in fresh_processes:
                if item["source"] == "external" and isinstance(item.get("pid"), int):
                    external_by_pid.setdefault(int(item["pid"]), item)
            for external_pid, item in external_by_pid.items():
                await asyncio.to_thread(
                    stop_confirmed_port_process,
                    int(item["port"]),
                    external_pid,
                )
            await self._ensure_runtime_ports_free(service)
            await self.process_compose.reload_configuration()
            self._config_sync_warning = None
            await self._start_runtime_processes(service)
            if service.active_config is not None:
                self.store.set_active_config(service_id, None)
            self._takeover_previews.pop(service.id, None)
        return {
            "requires_confirmation": False,
            "service_id": service_id,
            "operation": "takeover",
            "stopped_pids": sorted(actual),
            "stopped_pid": next(iter(actual)) if len(actual) == 1 else None,
        }

    async def stop_external_service(
        self,
        service_id: str,
        *,
        confirm: bool,
        pids: list[int] | None,
    ) -> dict[str, Any]:
        """Stop externally started processes without restarting the service."""
        service = self.store.get_service(service_id)
        self._assert_enabled(service)
        view = await self.get_service_view(service_id)
        if view["state"] != "External Running":
            raise HubConflict("not_external", "当前服务不是外部运行状态")
        candidate_ports = [int(view.get("effective_port") or service.port)]
        candidate_ports.extend(item.port for item in service.items()[1:])
        ports = list(dict.fromkeys(candidate_ports))
        inspected = await asyncio.gather(
            *(asyncio.to_thread(inspect_listening_port, port) for port in ports),
        )
        processes = [process for process in inspected if process is not None]
        if not processes:
            raise ProcessInspectionError("登记端口已无外部进程监听，请刷新状态")
        if not confirm:
            return {
                "requires_confirmation": True,
                "service": service.to_dict(),
                "processes": processes,
                "message": "将停止以上外部进程；服务不会自动重启。",
            }
        expected = sorted(pids or [])
        actual = sorted(int(process["pid"]) for process in processes)
        if expected != actual:
            raise HubConflict(
                "pid_confirmation_required",
                "确认请求必须包含预览中的全部 PID",
            )
        for process in processes:
            await asyncio.to_thread(
                stop_confirmed_port_process,
                int(process["port"]),
                int(process["pid"]),
            )
        return {
            "requires_confirmation": False,
            "service_id": service_id,
            "operation": "stop_external",
            "stopped_pids": actual,
        }

    async def clear_last_run(self, service_id: str) -> dict[str, Any]:
        """Drop the stored last-run record so its summary stops showing."""
        service = self.store.get_service(service_id)
        _, raw_states = await self._controller_and_states()
        acknowledged_exits: dict[str, int] = {}
        for item in self._all_runtime_items(service):
            process_id = process_id_for(service.id, item.id)
            raw_state = raw_states.get(process_id) or {}
            pid = raw_state.get("pid")
            if isinstance(pid, int) and pid > 0 and not bool(raw_state.get("is_running")):
                acknowledged_exits[process_id] = pid
        await asyncio.to_thread(
            self.run_history.clear_last_run,
            service_id,
            acknowledged_exits=acknowledged_exits,
        )
        return {"service_id": service_id, "cleared": True}

    async def get_logs(self, service_id: str, limit: int) -> dict[str, Any]:
        service = self.store.get_service(service_id)
        self._assert_enabled(service)
        controller, raw_states = await self._controller_and_states()
        if not controller["online"]:
            raise ControllerOffline("Process Compose Controller Offline")
        process_running = any(
            bool((raw_states.get(process_id) or {}).get("is_running", False))
            for process_id in (
                process_id_for(service.id, item.id)
                for item in self._all_runtime_items(service)
            )
        )
        try:
            logs = await self._combined_logs(service, limit=limit)
        except ProcessComposeError:
            # An externally started instance is not supervised by the
            # controller, so it has no "current" logs; keep the archive usable.
            if process_running:
                raise
            logs = []
        await asyncio.to_thread(self.log_archive.save_latest, service.id, logs)
        previous = await asyncio.to_thread(
            self.log_archive.read_previous,
            service.id,
        )
        current_diagnosis = classify_logs(logs)
        previous_diagnosis = classify_logs(previous)
        last_run = await asyncio.to_thread(
            self.run_history.get_last_run,
            service.id,
        )
        current_last_error = (
            current_diagnosis["last_error"] if process_running else None
        )
        previous_last_error = (
            previous_diagnosis["last_error"] or (last_run or {}).get("last_error")
        )
        # `last_error` remains as a compatibility field for existing clients,
        # but reading logs no longer mutates the service's live status.
        last_error = current_diagnosis["last_error"] or previous_last_error
        return {
            "service_id": service.id,
            "logs": logs,
            "entries": current_diagnosis["entries"],
            "previous_logs": previous,
            "previous_entries": previous_diagnosis["entries"],
            "last_error": last_error,
            "current_last_error": current_last_error,
            "previous_last_error": previous_last_error,
            "stdout_lines": current_diagnosis["stdout_lines"],
            "stderr_lines": current_diagnosis["stderr_lines"],
            "limit": limit,
        }

    async def clear_logs(self, service_id: str) -> dict[str, Any]:
        service = self.store.get_service(service_id)
        self._assert_enabled(service)
        for item in self._all_runtime_items(service):
            await self.process_compose.clear_logs(
                process_id_for(service.id, item.id)
            )
        await asyncio.to_thread(self.log_archive.clear_latest, service.id)
        return {
            "service_id": service.id,
            "cleared": True,
            "previous_preserved": True,
        }

    async def open_service_directory(self, service_id: str) -> dict[str, Any]:
        service = self.store.get_service(service_id)
        if not Path(service.working_dir).is_dir():
            raise HubConflict("missing_directory", f"项目目录不存在：{service.working_dir}")
        try:
            await asyncio.to_thread(_open_directory, service.working_dir)
        except OSError as exc:
            raise HubConflict("open_directory_failed", f"无法打开项目目录：{exc}") from exc
        return {"service_id": service.id, "opened": service.working_dir}
