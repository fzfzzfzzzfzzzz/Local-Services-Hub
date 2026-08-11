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
        views = await self.status_resolver.resolve_all(
            services,
            raw_states,
            controller_online=bool(controller["online"]),
        )
        external_views = [item for item in views if item["state"] == "External Running"]
        if external_views:
            inspected = await asyncio.gather(
                *(
                    asyncio.to_thread(
                        inspect_listening_port,
                        int(item.get("effective_port") or item["port"]),
                    )
                    for item in external_views
                ),
                return_exceptions=True,
            )
            for view, process in zip(external_views, inspected, strict=True):
                if isinstance(process, dict):
                    view["pid"] = process.get("pid")
                    view["started_at"] = process.get("started_at")
                    service = next(
                        item for item in services if item.id == view["id"]
                    )
                    view["port_occupant"] = self._port_conflict(
                        service,
                        process,
                        port=int(view.get("effective_port") or service.port),
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

        services_by_id = {service.id: service for service in services}
        views_by_id = {str(view["id"]): view for view in views}
        for view in views:
            service_id = str(view["id"])
            diagnosis = diagnostics.get(service_id, {})
            last_error = diagnosis.get("last_error") or view.get("error")
            view["last_error"] = last_error
            raw_state = raw_states.get(process_id_for(service_id)) or {}
            view["last_run"] = await asyncio.to_thread(
                self.run_history.observe,
                service_id,
                raw_state,
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
                "issues": counts["Unhealthy"] + counts["Error"],
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
                await self._archive_current_logs(service)
                await asyncio.to_thread(
                    self.run_history.mark_expected_exit,
                    service.id,
                    "restart",
                )
                await self._restart_runtime_processes(service)
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

    async def _restart_runtime_processes(self, service: ServiceDefinition) -> None:
        desired = {item.id: item for item in service.items()}
        active = {
            item.id: item
            for item in (
                service.active_config.items()
                if service.active_config is not None
                else service.items()
            )
        }
        for runtime_id in reversed(list(active)):
            if runtime_id in desired:
                continue
            try:
                await self.process_compose.stop_process(
                    process_id_for(service.id, runtime_id)
                )
            except ProcessComposeError:
                pass
        for runtime_id in desired:
            process_id = process_id_for(service.id, runtime_id)
            if runtime_id in active:
                await self.process_compose.restart_process(process_id)
            else:
                await self.process_compose.start_process(process_id)

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
        if view["state"] not in MANAGED_STATES:
            raise HubConflict("not_managed", "只有 Managed Running 服务可以重启")
        await self.process_compose.reload_configuration()
        self._config_sync_warning = None
        await self._archive_current_logs(service)
        await asyncio.to_thread(
            self.run_history.mark_expected_exit,
            service.id,
            "restart",
        )
        await self._restart_runtime_processes(service)
        if self.store.get_service(service_id).active_config is not None:
            self.store.set_active_config(service_id, None)
        return {"service_id": service_id, "operation": "restart"}

    async def takeover_service(
        self,
        service_id: str,
        *,
        confirm: bool,
        pid: int | None,
    ) -> dict[str, Any]:
        service = self.store.get_service(service_id)
        self._assert_enabled(service)
        if len(service.items()) > 1:
            raise HubConflict(
                "multi_runtime_takeover_unsupported",
                "多运行项服务需要先手动停止所有外部端口，再从管理器启动",
            )
        view = await self.get_service_view(service_id)
        if view["state"] != "External Running":
            raise HubConflict("not_external", "当前服务不是 External Running")
        target_port = int(view.get("effective_port") or service.port)
        inspected = await asyncio.to_thread(inspect_listening_port, target_port)
        if inspected is None:
            raise ProcessInspectionError(f"端口 {target_port} 已不再监听")
        preview = {
            "service": service.to_dict(),
            "process": inspected,
            "message": "将停止当前外部实例，然后改由 Process Compose 启动。",
        }
        if not confirm:
            return {"requires_confirmation": True, **preview}
        if pid is None or pid != inspected["pid"]:
            raise HubConflict("pid_confirmation_required", "确认请求必须包含预览中的 PID")
        if not view["controller_online"]:
            raise ControllerOffline("Process Compose Controller Offline")

        await asyncio.to_thread(stop_confirmed_port_process, target_port, pid)
        await self.process_compose.reload_configuration()
        self._config_sync_warning = None
        await self._start_runtime_processes(service)
        if service.active_config is not None:
            self.store.set_active_config(service_id, None)
        return {
            "requires_confirmation": False,
            "service_id": service_id,
            "operation": "takeover",
            "stopped_pid": pid,
        }

    async def get_logs(self, service_id: str, limit: int) -> dict[str, Any]:
        service = self.store.get_service(service_id)
        self._assert_enabled(service)
        logs = await self._combined_logs(service, limit=limit)
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
        last_error = (
            current_diagnosis["last_error"]
            or previous_diagnosis["last_error"]
            or (last_run or {}).get("last_error")
        )
        await asyncio.to_thread(
            self.run_history.set_last_error,
            service.id,
            last_error,
        )
        return {
            "service_id": service.id,
            "logs": logs,
            "entries": current_diagnosis["entries"],
            "previous_logs": previous,
            "previous_entries": previous_diagnosis["entries"],
            "last_error": last_error,
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
