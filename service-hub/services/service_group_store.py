from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shutil
import threading
from typing import Any
from uuid import uuid4


ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ServiceGroupError(RuntimeError):
    """A service group could not be loaded, validated, or persisted."""


class ServiceGroupNotFoundError(ServiceGroupError):
    """The requested service group does not exist."""


@dataclass(frozen=True, slots=True)
class ServiceGroup:
    id: str
    name: str
    description: str
    services: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["services"] = list(self.services)
        return payload


def _text(value: Any, field: str, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ServiceGroupError(f"{field} 必须是字符串")
    result = value.strip()
    if required and not result:
        raise ServiceGroupError(f"{field} 不能为空")
    return result


def validate_group(
    raw: dict[str, Any],
    *,
    service_ids: set[str],
    group_id: str,
) -> ServiceGroup:
    if not isinstance(raw, dict):
        raise ServiceGroupError("服务组配置必须是对象")
    if not ID_PATTERN.fullmatch(group_id):
        raise ServiceGroupError("服务组 ID 无效")
    raw_services = raw.get("services")
    if not isinstance(raw_services, list) or not raw_services:
        raise ServiceGroupError("服务组至少需要包含一个服务")
    services: list[str] = []
    for index, item in enumerate(raw_services):
        service_id = _text(item, f"services[{index}]", required=True)
        if service_id not in service_ids:
            raise ServiceGroupError(f"服务组引用了不存在的服务：{service_id}")
        if service_id not in services:
            services.append(service_id)
    return ServiceGroup(
        id=group_id,
        name=_text(raw.get("name"), "name", required=True),
        description=_text(raw.get("description"), "description"),
        services=tuple(services),
    )


class ServiceGroupStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.backup_path = self.path.with_name(f"{self.path.name}.bak")
        self.temp_path = self.path.with_name(f"{self.path.name}.tmp")
        self._lock = threading.RLock()
        self._groups: list[ServiceGroup] = []
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self._write([])
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ServiceGroupError(f"无法读取 {self.path.name}：{exc}") from exc
            items = raw.get("groups") if isinstance(raw, dict) else None
            if not isinstance(items, list):
                raise ServiceGroupError(f"{self.path.name} 必须包含 groups 数组")
            groups: list[ServiceGroup] = []
            seen: set[str] = set()
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    raise ServiceGroupError(f"groups[{index}] 必须是对象")
                group_id = _text(item.get("id"), f"groups[{index}].id", required=True)
                if not ID_PATTERN.fullmatch(group_id) or group_id in seen:
                    raise ServiceGroupError(f"groups[{index}].id 无效或重复")
                raw_services = item.get("services")
                if not isinstance(raw_services, list):
                    raise ServiceGroupError(f"groups[{index}].services 必须是数组")
                services = tuple(
                    dict.fromkeys(
                        _text(value, f"groups[{index}].services", required=True)
                        for value in raw_services
                    )
                )
                groups.append(
                    ServiceGroup(
                        id=group_id,
                        name=_text(item.get("name"), f"groups[{index}].name", required=True),
                        description=_text(item.get("description"), f"groups[{index}].description"),
                        services=services,
                    )
                )
                seen.add(group_id)
            self._groups = groups

    def _write(self, groups: list[ServiceGroup]) -> None:
        payload = {
            "groups": [group.to_dict() for group in groups],
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            json.loads(self.temp_path.read_text(encoding="utf-8"))
            if self.path.exists():
                shutil.copy2(self.path, self.backup_path)
            os.replace(self.temp_path, self.path)
        except Exception as exc:
            self.temp_path.unlink(missing_ok=True)
            raise ServiceGroupError(f"写入 {self.path.name} 失败：{exc}") from exc

    def list_groups(self) -> list[ServiceGroup]:
        with self._lock:
            return list(self._groups)

    def get_group(self, group_id: str) -> ServiceGroup:
        with self._lock:
            for group in self._groups:
                if group.id == group_id:
                    return group
        raise ServiceGroupNotFoundError(f"未找到服务组：{group_id}")

    def create_group(
        self,
        raw: dict[str, Any],
        *,
        service_ids: set[str],
    ) -> ServiceGroup:
        with self._lock:
            group_id = f"grp_{uuid4().hex[:12]}"
            group = validate_group(raw, service_ids=service_ids, group_id=group_id)
            groups = [*self._groups, group]
            self._write(groups)
            self._groups = groups
            return group

    def update_group(
        self,
        group_id: str,
        raw: dict[str, Any],
        *,
        service_ids: set[str],
    ) -> ServiceGroup:
        with self._lock:
            groups = list(self._groups)
            for index, current in enumerate(groups):
                if current.id != group_id:
                    continue
                group = validate_group(raw, service_ids=service_ids, group_id=group_id)
                groups[index] = group
                self._write(groups)
                self._groups = groups
                return group
        raise ServiceGroupNotFoundError(f"未找到服务组：{group_id}")

    def delete_group(self, group_id: str) -> ServiceGroup:
        with self._lock:
            groups = list(self._groups)
            for index, group in enumerate(groups):
                if group.id == group_id:
                    deleted = groups.pop(index)
                    self._write(groups)
                    self._groups = groups
                    return deleted
        raise ServiceGroupNotFoundError(f"未找到服务组：{group_id}")

    def remove_service_references(self, service_id: str) -> list[str]:
        with self._lock:
            affected: list[str] = []
            groups: list[ServiceGroup] = []
            for group in self._groups:
                if service_id not in group.services:
                    groups.append(group)
                    continue
                affected.append(group.id)
                remaining = tuple(item for item in group.services if item != service_id)
                if remaining:
                    groups.append(
                        ServiceGroup(
                            id=group.id,
                            name=group.name,
                            description=group.description,
                            services=remaining,
                        )
                    )
            if affected:
                self._write(groups)
                self._groups = groups
            return affected
