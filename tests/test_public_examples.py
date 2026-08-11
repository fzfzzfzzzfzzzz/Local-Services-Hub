from __future__ import annotations

from pathlib import Path

from services.service_group_store import ServiceGroupStore
from services.service_store import ServiceStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_public_examples_are_valid_and_safe_by_default() -> None:
    services = ServiceStore(PROJECT_ROOT / "services.example.json").list_services()
    groups = ServiceGroupStore(
        PROJECT_ROOT / "service-groups.example.json"
    ).list_groups()

    service_ids = {service.id for service in services}
    assert services
    assert all(not service.enabled for service in services)
    assert all(set(group.services) <= service_ids for group in groups)
