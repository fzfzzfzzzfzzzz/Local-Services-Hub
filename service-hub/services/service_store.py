from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import re
import shutil
import threading
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlparse
from uuid import uuid4


RESERVED_PORTS = {8750, 8751}
SERVICE_TYPES = {"frontend", "backend", "fullstack", "worker", "plugin", "other"}
HEALTH_CHECK_TYPES = {"process", "tcp", "http"}
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ServiceStoreError(RuntimeError):
    """Base error for the persistent service registry."""


class ServiceValidationError(ServiceStoreError):
    """A service definition failed validation."""


class ServiceNotFoundError(ServiceStoreError):
    """The requested registered service does not exist."""


class CorruptStoreError(ServiceStoreError):
    """The primary JSON store is corrupt and must be restored explicitly."""


@dataclass(frozen=True, slots=True)
class RuntimeItem:
    id: str
    port: int
    command: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ActiveConfiguration:
    port: int
    working_dir: str
    command: str
    url: str | None
    health_url: str | None
    health_check_type: str = "tcp"
    health_expected_status: int = 200
    runtime_items: tuple[RuntimeItem, ...] = ()

    def __post_init__(self) -> None:
        if not self.runtime_items:
            object.__setattr__(
                self,
                "runtime_items",
                (RuntimeItem("main", self.port, self.command),),
            )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["runtime_items"] = [item.to_dict() for item in self.items()]
        return payload

    def items(self) -> tuple[RuntimeItem, ...]:
        return self.runtime_items or (RuntimeItem("main", self.port, self.command),)


@dataclass(frozen=True, slots=True)
class ServiceDefinition:
    id: str
    name: str
    port: int
    working_dir: str
    command: str
    url: str | None
    type: str
    note: str
    health_url: str | None
    enabled: bool
    health_check_type: str = "tcp"
    health_expected_status: int = 200
    dependencies: tuple[str, ...] = ()
    runtime_items: tuple[RuntimeItem, ...] = ()
    active_config: ActiveConfiguration | None = None

    def __post_init__(self) -> None:
        if not self.runtime_items:
            object.__setattr__(
                self,
                "runtime_items",
                (RuntimeItem("main", self.port, self.command),),
            )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "name": self.name,
            "port": self.port,
            "working_dir": self.working_dir,
            "command": self.command,
            "url": self.url,
            "type": self.type,
            "note": self.note,
            "health_url": self.health_url,
            "health_check_type": self.health_check_type,
            "health_expected_status": self.health_expected_status,
            "dependencies": list(self.dependencies),
            "runtime_items": [item.to_dict() for item in self.items()],
            "enabled": self.enabled,
        }
        if self.active_config is not None:
            payload["active_config"] = self.active_config.to_dict()
        return payload

    def items(self) -> tuple[RuntimeItem, ...]:
        return self.runtime_items or (RuntimeItem("main", self.port, self.command),)

    def runtime_config(self) -> ActiveConfiguration:
        return ActiveConfiguration(
            port=self.port,
            working_dir=self.working_dir,
            command=self.command,
            url=self.url,
            health_url=self.health_url,
            health_check_type=self.health_check_type,
            health_expected_status=self.health_expected_status,
            runtime_items=self.items(),
        )


def _text(value: Any, field: str, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ServiceValidationError(f"{field} 必须是字符串")
    normalized = value.strip()
    if required and not normalized:
        raise ServiceValidationError(f"{field} 不能为空")
    return normalized


def _url(value: Any, field: str) -> str | None:
    normalized = _text(value, field)
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ServiceValidationError(f"{field} 必须是完整的 HTTP(S) URL")
    return normalized


def _port(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ServiceValidationError(f"{field} 必须是整数")
    if not 1 <= value <= 65535:
        raise ServiceValidationError(f"{field} 必须在 1–65535 之间")
    if value in RESERVED_PORTS:
        raise ServiceValidationError(f"端口 {value} 为 Service Hub 控制层保留端口")
    return value


def _runtime_items(
    value: Any,
    *,
    fallback_port: int,
    fallback_command: str,
    field: str = "runtime_items",
) -> tuple[RuntimeItem, ...]:
    if value is None:
        return (RuntimeItem("main", fallback_port, fallback_command),)
    if not isinstance(value, list) or not value:
        raise ServiceValidationError(f"{field} 必须是至少包含一项的数组")
    if len(value) > 12:
        raise ServiceValidationError(f"{field} 最多支持 12 个运行项")
    result: list[RuntimeItem] = []
    seen_ids: set[str] = set()
    seen_ports: set[int] = set()
    for index, raw_item in enumerate(value):
        item_field = f"{field}[{index}]"
        if not isinstance(raw_item, dict):
            raise ServiceValidationError(f"{item_field} 必须是对象")
        item_id = "main" if index == 0 else _text(
            raw_item.get("id") or f"item_{index + 1}",
            f"{item_field}.id",
            required=True,
        ).lower()
        if not ID_PATTERN.fullmatch(item_id):
            raise ServiceValidationError(f"{item_field}.id 不是有效运行项 ID")
        if item_id in seen_ids:
            raise ServiceValidationError(f"{field} 包含重复 ID：{item_id}")
        port = (
            fallback_port
            if index == 0
            else _port(raw_item.get("port"), f"{item_field}.port")
        )
        if port in seen_ports:
            raise ServiceValidationError(f"同一服务不能重复登记端口 {port}")
        command = (
            fallback_command
            if index == 0
            else _text(raw_item.get("command"), f"{item_field}.command", required=True)
        )
        result.append(RuntimeItem(item_id, port, command))
        seen_ids.add(item_id)
        seen_ports.add(port)
    return tuple(result)


def _active_configuration(
    value: Any,
    *,
    service_type: str = "other",
) -> ActiveConfiguration | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ServiceValidationError("active_config 必须是对象或 null")
    port = _port(value.get("port"), "active_config.port")
    command = _text(value.get("command"), "active_config.command", required=True)
    runtime_items = _runtime_items(
        value.get("runtime_items"),
        fallback_port=port,
        fallback_command=command,
        field="active_config.runtime_items",
    )
    port = runtime_items[0].port
    command = runtime_items[0].command
    health_url = _url(value.get("health_url"), "active_config.health_url")
    health_check_type = _health_check_type(
        value.get("health_check_type"),
        health_url=health_url,
        service_type=service_type,
        field="active_config.health_check_type",
    )
    expected_status = _expected_status(
        value.get("health_expected_status", 200),
        "active_config.health_expected_status",
    )
    if health_check_type == "http" and health_url is None:
        raise ServiceValidationError("active_config.health_url 在 HTTP 检查模式下不能为空")
    return ActiveConfiguration(
        port=port,
        working_dir=_text(
            value.get("working_dir"),
            "active_config.working_dir",
            required=True,
        ),
        command=command,
        url=_url(value.get("url"), "active_config.url"),
        health_url=health_url,
        health_check_type=health_check_type,
        health_expected_status=expected_status,
        runtime_items=runtime_items,
    )


def _health_check_type(
    value: Any,
    *,
    health_url: str | None,
    service_type: str,
    field: str = "health_check_type",
) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if health_url:
            return "http"
        return "process" if service_type == "worker" else "tcp"
    normalized = _text(value, field).lower()
    if normalized not in HEALTH_CHECK_TYPES:
        raise ServiceValidationError(f"{field} 必须是 process、tcp 或 http")
    return normalized


def _expected_status(value: Any, field: str = "health_expected_status") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        raise ServiceValidationError(f"{field} 必须是 100–599 之间的整数")
    return value


def _dependencies(value: Any, service_id: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ServiceValidationError("dependencies 必须是服务 ID 数组")
    result: list[str] = []
    for index, item in enumerate(value):
        dependency_id = _text(item, f"dependencies[{index}]", required=True)
        if not ID_PATTERN.fullmatch(dependency_id):
            raise ServiceValidationError(f"dependencies[{index}] 不是有效服务 ID")
        if dependency_id == service_id:
            raise ServiceValidationError("服务不能依赖自身")
        if dependency_id not in result:
            result.append(dependency_id)
    return tuple(result)


def validate_dependency_graph(services: list[ServiceDefinition]) -> None:
    by_id = {service.id: service for service in services}
    for service in services:
        missing = [item for item in service.dependencies if item not in by_id]
        if missing:
            raise ServiceValidationError(
                f"服务“{service.name}”引用了不存在的依赖：{', '.join(missing)}"
            )

    visiting: list[str] = []
    visited: set[str] = set()

    def visit(service_id: str) -> None:
        if service_id in visited:
            return
        if service_id in visiting:
            start = visiting.index(service_id)
            cycle_ids = visiting[start:] + [service_id]
            cycle = " → ".join(by_id[item].name for item in cycle_ids)
            raise ServiceValidationError(f"检测到循环依赖：{cycle}")
        visiting.append(service_id)
        for dependency_id in by_id[service_id].dependencies:
            visit(dependency_id)
        visiting.pop()
        visited.add(service_id)

    for service in services:
        visit(service.id)


def validate_service(
    raw: dict[str, Any],
    *,
    existing: list[ServiceDefinition],
    service_id: str | None = None,
) -> ServiceDefinition:
    if not isinstance(raw, dict):
        raise ServiceValidationError("服务配置必须是对象")

    identifier = service_id or _text(raw.get("id"), "id", required=True)
    if not ID_PATTERN.fullmatch(identifier):
        raise ServiceValidationError("id 必须以小写字母开头，且只包含字母、数字、_ 或 -")

    name = _text(raw.get("name"), "name", required=True)
    port = _port(raw.get("port"), "port")

    working_dir = _text(raw.get("working_dir"), "working_dir", required=True)
    if not Path(working_dir).is_dir():
        raise ServiceValidationError(f"项目目录不存在：{working_dir}")
    command = _text(raw.get("command"), "command", required=True)
    runtime_items = _runtime_items(
        raw.get("runtime_items"),
        fallback_port=port,
        fallback_command=command,
    )
    port = runtime_items[0].port
    command = runtime_items[0].command

    service_type = _text(raw.get("type") or "other", "type").lower()
    if service_type not in SERVICE_TYPES:
        raise ServiceValidationError(
            "type 必须是 frontend、backend、fullstack、worker、plugin 或 other"
        )
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ServiceValidationError("enabled 必须是布尔值")

    desired_ports = {runtime.port for runtime in runtime_items}
    for item in existing:
        if item.id == identifier:
            continue
        occupied_ports = {runtime.port for runtime in item.items()}
        if item.active_config is not None:
            occupied_ports.update(runtime.port for runtime in item.active_config.items())
        duplicate_ports = sorted(desired_ports & occupied_ports)
        if duplicate_ports:
            raise ServiceValidationError(
                f"端口 {duplicate_ports[0]} 已登记给服务“{item.name}”"
            )

    url_value = raw.get("url")
    if url_value is None or (isinstance(url_value, str) and not url_value.strip()):
        url_value = f"http://127.0.0.1:{port}"

    health_url = _url(raw.get("health_url"), "health_url")
    health_check_type = _health_check_type(
        raw.get("health_check_type"),
        health_url=health_url,
        service_type=service_type,
    )
    health_expected_status = _expected_status(raw.get("health_expected_status", 200))
    if health_check_type == "http" and health_url is None:
        raise ServiceValidationError("health_url 在 HTTP 检查模式下不能为空")

    return ServiceDefinition(
        id=identifier,
        name=name,
        port=port,
        working_dir=str(Path(working_dir).resolve()),
        command=command,
        url=_url(url_value, "url"),
        type=service_type,
        note=_text(raw.get("note"), "note"),
        health_url=health_url,
        enabled=enabled,
        health_check_type=health_check_type,
        health_expected_status=health_expected_status,
        dependencies=_dependencies(raw.get("dependencies"), identifier),
        runtime_items=runtime_items,
        active_config=_active_configuration(
            raw.get("active_config"),
            service_type=service_type,
        ),
    )


class ServiceStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.backup_path = self.path.with_name(f"{self.path.name}.bak")
        self.temp_path = self.path.with_name(f"{self.path.name}.tmp")
        self.rollback_path = self.path.with_name(f"{self.path.name}.rollback")
        self._lock = threading.RLock()
        self._services: list[ServiceDefinition] = []
        self.degraded_error: str | None = None
        self.using_backup = False
        self._recover_interrupted_commit()
        self._load_initial()

    def _recover_interrupted_commit(self) -> None:
        """Abort a configuration transaction interrupted before its commit point."""
        try:
            if self.rollback_path.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(self.rollback_path, self.path)
            self.temp_path.unlink(missing_ok=True)
        except OSError as exc:
            raise ServiceStoreError(f"恢复未完成的配置事务失败：{exc}") from exc

    def _parse(self, path: Path) -> list[ServiceDefinition]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CorruptStoreError(f"无法读取 {path.name}：{exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("services"), list):
            raise CorruptStoreError(f"{path.name} 必须包含 services 数组")

        services: list[ServiceDefinition] = []
        seen: set[str] = set()
        for index, item in enumerate(raw["services"]):
            try:
                identifier = item.get("id") if isinstance(item, dict) else None
                service = validate_service(item, existing=services, service_id=identifier)
            except ServiceValidationError as exc:
                raise CorruptStoreError(
                    f"{path.name} 的 services[{index}] 无效：{exc}"
                ) from exc
            if service.id in seen:
                raise CorruptStoreError(f"{path.name} 包含重复 id：{service.id}")
            seen.add(service.id)
            services.append(service)
        try:
            validate_dependency_graph(services)
        except ServiceValidationError as exc:
            raise CorruptStoreError(f"{path.name} 的依赖关系无效：{exc}") from exc
        return services

    def _load_initial(self) -> None:
        with self._lock:
            if not self.path.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._write_services([])
                return
            try:
                self._services = self._parse(self.path)
                return
            except CorruptStoreError as primary_error:
                self.degraded_error = str(primary_error)

            if self.backup_path.exists():
                try:
                    self._services = self._parse(self.backup_path)
                    self.using_backup = True
                    self.degraded_error = (
                        f"{self.degraded_error}；当前只读展示备份内容，请确认后恢复备份"
                    )
                    return
                except CorruptStoreError as backup_error:
                    self.degraded_error = f"{self.degraded_error}；备份也不可用：{backup_error}"
            self._services = []

    def _assert_writable(self) -> None:
        if self.degraded_error is not None:
            raise CorruptStoreError(self.degraded_error)

    @staticmethod
    def _encode_services(services: list[ServiceDefinition]) -> str:
        payload = {"services": [service.to_dict() for service in services]}
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    def _write_temp(self, services: list[ServiceDefinition]) -> None:
        encoded = self._encode_services(services)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        parsed = json.loads(self.temp_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict) or not isinstance(parsed.get("services"), list):
            raise ValueError("prepared services.json is missing the services array")

    def _write_services(self, services: list[ServiceDefinition]) -> None:
        try:
            self._write_temp(services)
            if self.path.exists():
                shutil.copy2(self.path, self.backup_path)
            os.replace(self.temp_path, self.path)
        except Exception as exc:
            try:
                self.temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ServiceStoreError(f"写入 {self.path.name} 失败：{exc}") from exc

    def prepare_services(self, services: list[ServiceDefinition]) -> None:
        """Durably stage a validated registry without changing the live file."""
        with self._lock:
            self._assert_writable()
            try:
                self.rollback_path.unlink(missing_ok=True)
                self._write_temp(services)
            except Exception as exc:
                self.temp_path.unlink(missing_ok=True)
                if isinstance(exc, ServiceStoreError):
                    raise
                raise ServiceStoreError(f"准备 {self.path.name} 失败：{exc}") from exc

    def commit_prepared(self, services: list[ServiceDefinition]) -> None:
        """Install a staged registry while keeping the previous file for rollback."""
        with self._lock:
            if not self.temp_path.exists():
                raise ServiceStoreError(f"没有已准备的 {self.path.name}")
            had_current = self.path.exists()
            try:
                if had_current:
                    os.replace(self.path, self.rollback_path)
                os.replace(self.temp_path, self.path)
                self._services = list(services)
            except Exception as exc:
                if self.rollback_path.exists():
                    os.replace(self.rollback_path, self.path)
                self.temp_path.unlink(missing_ok=True)
                raise ServiceStoreError(f"提交 {self.path.name} 失败：{exc}") from exc

    def finalize_prepared(self) -> None:
        """Mark the staged registry committed and retain the old file as .bak."""
        with self._lock:
            try:
                if self.rollback_path.exists():
                    os.replace(self.rollback_path, self.backup_path)
            except OSError as exc:
                raise ServiceStoreError(f"完成 {self.path.name} 事务失败：{exc}") from exc

    def rollback_prepared(self, services: list[ServiceDefinition]) -> None:
        with self._lock:
            try:
                if self.rollback_path.exists():
                    os.replace(self.rollback_path, self.path)
                self.temp_path.unlink(missing_ok=True)
                self._services = list(services)
            except OSError as exc:
                raise ServiceStoreError(f"回滚 {self.path.name} 失败：{exc}") from exc

    def discard_prepared(self) -> None:
        with self._lock:
            try:
                self.temp_path.unlink(missing_ok=True)
            except OSError as exc:
                raise ServiceStoreError(f"清理 {self.temp_path.name} 失败：{exc}") from exc

    def list_services(self) -> list[ServiceDefinition]:
        with self._lock:
            return list(self._services)

    def get_service(self, service_id: str) -> ServiceDefinition:
        with self._lock:
            for service in self._services:
                if service.id == service_id:
                    return service
        raise ServiceNotFoundError(f"未找到服务：{service_id}")

    def _mutate(self, operation: Callable[[list[ServiceDefinition]], Any]) -> Any:
        with self._lock:
            self._assert_writable()
            candidate = list(self._services)
            result = operation(candidate)
            self._write_services(candidate)
            self._services = candidate
            return result

    def candidate_create(
        self,
        raw: dict[str, Any],
    ) -> tuple[list[ServiceDefinition], ServiceDefinition]:
        with self._lock:
            self._assert_writable()
            candidate = list(self._services)
            identifier = f"svc_{uuid4().hex[:12]}"
            service = validate_service(raw, existing=candidate, service_id=identifier)
            candidate.append(service)
            validate_dependency_graph(candidate)
            return candidate, service

    def candidate_update(
        self,
        service_id: str,
        raw: dict[str, Any],
        *,
        active_config: ActiveConfiguration | None | object = ...,
    ) -> tuple[list[ServiceDefinition], ServiceDefinition]:
        with self._lock:
            self._assert_writable()
            candidate = list(self._services)
            for index, current in enumerate(candidate):
                if current.id != service_id:
                    continue
                service = validate_service(raw, existing=candidate, service_id=current.id)
                service = replace(
                    service,
                    active_config=(
                        current.active_config if active_config is ... else active_config
                    ),
                )
                candidate[index] = service
                validate_dependency_graph(candidate)
                return candidate, service
        raise ServiceNotFoundError(f"未找到服务：{service_id}")

    def candidate_delete(
        self,
        service_id: str,
    ) -> tuple[list[ServiceDefinition], ServiceDefinition]:
        with self._lock:
            self._assert_writable()
            candidate = list(self._services)
            for index, service in enumerate(candidate):
                if service.id == service_id:
                    dependents = [
                        item.name
                        for item in candidate
                        if service_id in item.dependencies
                    ]
                    if dependents:
                        raise ServiceValidationError(
                            f"该服务仍被以下服务依赖，不能移除：{'、'.join(dependents)}"
                        )
                    return candidate, candidate.pop(index)
        raise ServiceNotFoundError(f"未找到服务：{service_id}")

    def create_service(self, raw: dict[str, Any]) -> ServiceDefinition:
        def create(candidate: list[ServiceDefinition]) -> ServiceDefinition:
            identifier = f"svc_{uuid4().hex[:12]}"
            service = validate_service(raw, existing=candidate, service_id=identifier)
            candidate.append(service)
            validate_dependency_graph(candidate)
            return service

        return self._mutate(create)

    def update_service(
        self,
        service_id: str,
        raw: dict[str, Any],
        *,
        active_config: ActiveConfiguration | None | object = ...,
    ) -> ServiceDefinition:
        def update(candidate: list[ServiceDefinition]) -> ServiceDefinition:
            for index, current in enumerate(candidate):
                if current.id == service_id:
                    service = validate_service(
                        raw,
                        existing=candidate,
                        service_id=current.id,
                    )
                    service = replace(
                        service,
                        active_config=(
                            current.active_config
                            if active_config is ...
                            else active_config
                        ),
                    )
                    candidate[index] = service
                    validate_dependency_graph(candidate)
                    return service
            raise ServiceNotFoundError(f"未找到服务：{service_id}")

        return self._mutate(update)

    def set_active_config(
        self,
        service_id: str,
        active_config: ActiveConfiguration | None,
    ) -> ServiceDefinition:
        def update(candidate: list[ServiceDefinition]) -> ServiceDefinition:
            for index, current in enumerate(candidate):
                if current.id == service_id:
                    updated = replace(current, active_config=active_config)
                    candidate[index] = updated
                    return updated
            raise ServiceNotFoundError(f"未找到服务：{service_id}")

        return self._mutate(update)

    def delete_service(self, service_id: str) -> ServiceDefinition:
        def delete(candidate: list[ServiceDefinition]) -> ServiceDefinition:
            for index, service in enumerate(candidate):
                if service.id == service_id:
                    dependents = [
                        item.name
                        for item in candidate
                        if service_id in item.dependencies
                    ]
                    if dependents:
                        raise ServiceValidationError(
                            f"该服务仍被以下服务依赖，不能移除：{'、'.join(dependents)}"
                        )
                    return candidate.pop(index)
            raise ServiceNotFoundError(f"未找到服务：{service_id}")

        return self._mutate(delete)

    def restore_backup(self) -> None:
        with self._lock:
            if not self.backup_path.exists():
                raise CorruptStoreError("没有可恢复的 services.json.bak")
            restored = self._parse(self.backup_path)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            corrupt_copy = self.path.with_name(f"{self.path.name}.corrupt-{timestamp}")
            try:
                if self.path.exists():
                    shutil.copy2(self.path, corrupt_copy)
                encoded = json.dumps(
                    {"services": [service.to_dict() for service in restored]},
                    ensure_ascii=False,
                    indent=2,
                ) + "\n"
                self.temp_path.write_text(encoded, encoding="utf-8", newline="\n")
                os.replace(self.temp_path, self.path)
            except OSError as exc:
                raise ServiceStoreError(f"恢复备份失败：{exc}") from exc
            self._services = restored
            self.degraded_error = None
            self.using_backup = False
