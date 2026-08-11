from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

import yaml


ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class RegistryError(ValueError):
    """Raised when the service registry is invalid."""


@dataclass(frozen=True, slots=True)
class ProcessDefinition:
    id: str
    role: str
    display_name: str
    port: int | None
    required: bool
    health_url: str | None
    starting_grace_seconds: int


@dataclass(frozen=True, slots=True)
class ProjectDefinition:
    id: str
    name: str
    description: str
    category: str
    namespace: str
    home_url: str | None
    processes: tuple[ProcessDefinition, ...]


@dataclass(frozen=True, slots=True)
class SceneDefinition:
    id: str
    name: str
    projects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ServiceRegistry:
    projects: dict[str, ProjectDefinition]
    scenes: dict[str, SceneDefinition]

    def get_project(self, project_id: str) -> ProjectDefinition:
        try:
            return self.projects[project_id]
        except KeyError as exc:
            raise KeyError(f"Unknown project: {project_id}") from exc

    def get_scene(self, scene_id: str) -> SceneDefinition:
        try:
            return self.scenes[scene_id]
        except KeyError as exc:
            raise KeyError(f"Unknown scene: {scene_id}") from exc


def _require_mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError(f"{location} must be a mapping")
    return value


def _require_non_empty_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{location} must be a non-empty string")
    return value.strip()


def _validate_id(value: Any, location: str) -> str:
    identifier = _require_non_empty_string(value, location)
    if not ID_PATTERN.fullmatch(identifier):
        raise RegistryError(
            f"{location} must match {ID_PATTERN.pattern!r}; got {identifier!r}"
        )
    return identifier


def _validate_local_url(value: Any, location: str) -> str | None:
    if value is None:
        return None
    url = _require_non_empty_string(value, location)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RegistryError(f"{location} must be an absolute HTTP(S) URL")
    if parsed.hostname not in LOCAL_HOSTS:
        raise RegistryError(f"{location} must point to localhost; got {parsed.hostname!r}")
    return url


def _parse_process(
    raw: Any,
    location: str,
    seen_process_ids: set[str],
) -> ProcessDefinition:
    data = _require_mapping(raw, location)
    process_id = _validate_id(data.get("id"), f"{location}.id")
    if process_id in seen_process_ids:
        raise RegistryError(f"Duplicate process id: {process_id}")
    seen_process_ids.add(process_id)

    port = data.get("port")
    if port is not None and (isinstance(port, bool) or not isinstance(port, int)):
        raise RegistryError(f"{location}.port must be an integer or null")
    if port is not None and not 1 <= port <= 65535:
        raise RegistryError(f"{location}.port must be between 1 and 65535")

    required = data.get("required", True)
    if not isinstance(required, bool):
        raise RegistryError(f"{location}.required must be a boolean")

    grace = data.get("starting_grace_seconds", 10)
    if isinstance(grace, bool) or not isinstance(grace, int) or not 0 <= grace <= 300:
        raise RegistryError(
            f"{location}.starting_grace_seconds must be an integer from 0 to 300"
        )

    role = _require_non_empty_string(data.get("role"), f"{location}.role")
    display_name = data.get("display_name", role.replace("_", " ").title())

    return ProcessDefinition(
        id=process_id,
        role=role,
        display_name=_require_non_empty_string(
            display_name, f"{location}.display_name"
        ),
        port=port,
        required=required,
        health_url=_validate_local_url(data.get("health_url"), f"{location}.health_url"),
        starting_grace_seconds=grace,
    )


def load_registry(path: str | Path) -> ServiceRegistry:
    registry_path = Path(path)
    if not registry_path.is_file():
        raise RegistryError(f"Registry file does not exist: {registry_path}")

    try:
        raw = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RegistryError(f"Invalid YAML in {registry_path}: {exc}") from exc

    root = _require_mapping(raw, "registry")
    raw_projects = _require_mapping(root.get("projects"), "registry.projects")
    if not raw_projects:
        raise RegistryError("registry.projects must contain at least one project")

    seen_process_ids: set[str] = set()
    projects: dict[str, ProjectDefinition] = {}
    for raw_project_id, raw_project in raw_projects.items():
        project_id = _validate_id(raw_project_id, "project id")
        data = _require_mapping(raw_project, f"projects.{project_id}")
        raw_processes = data.get("processes")
        if not isinstance(raw_processes, list) or not raw_processes:
            raise RegistryError(f"projects.{project_id}.processes must be a non-empty list")
        processes = tuple(
            _parse_process(
                item,
                f"projects.{project_id}.processes[{index}]",
                seen_process_ids,
            )
            for index, item in enumerate(raw_processes)
        )
        if not any(process.required for process in processes):
            raise RegistryError(f"projects.{project_id} needs at least one required process")

        projects[project_id] = ProjectDefinition(
            id=project_id,
            name=_require_non_empty_string(
                data.get("name"), f"projects.{project_id}.name"
            ),
            description=_require_non_empty_string(
                data.get("description", "Local service"),
                f"projects.{project_id}.description",
            ),
            category=_validate_id(
                data.get("category", "other"), f"projects.{project_id}.category"
            ),
            namespace=_validate_id(
                data.get("namespace"), f"projects.{project_id}.namespace"
            ),
            home_url=_validate_local_url(
                data.get("home_url"), f"projects.{project_id}.home_url"
            ),
            processes=processes,
        )

    raw_scenes = root.get("scenes", {})
    scenes_data = _require_mapping(raw_scenes, "registry.scenes")
    scenes: dict[str, SceneDefinition] = {}
    for raw_scene_id, raw_scene in scenes_data.items():
        scene_id = _validate_id(raw_scene_id, "scene id")
        data = _require_mapping(raw_scene, f"scenes.{scene_id}")
        raw_ids = data.get("projects")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise RegistryError(f"scenes.{scene_id}.projects must be a non-empty list")
        project_ids = tuple(
            _validate_id(item, f"scenes.{scene_id}.projects[{index}]")
            for index, item in enumerate(raw_ids)
        )
        missing = [item for item in project_ids if item not in projects]
        if missing:
            raise RegistryError(
                f"scenes.{scene_id} references unknown projects: {', '.join(missing)}"
            )
        scenes[scene_id] = SceneDefinition(
            id=scene_id,
            name=_require_non_empty_string(
                data.get("name"), f"scenes.{scene_id}.name"
            ),
            projects=project_ids,
        )

    return ServiceRegistry(projects=projects, scenes=scenes)

