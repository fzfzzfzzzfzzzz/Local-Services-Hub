from __future__ import annotations

from pathlib import Path

import pytest

from services.service_group_store import ServiceGroupError, ServiceGroupStore


def test_group_crud_and_remove_service_references(tmp_path: Path) -> None:
    store = ServiceGroupStore(tmp_path / "service-groups.json")
    group = store.create_group(
        {
            "name": "AI 创作",
            "description": "创作工具",
            "services": ["backend", "frontend"],
        },
        service_ids={"backend", "frontend"},
    )
    assert store.get_group(group.id).services == ("backend", "frontend")

    updated = store.update_group(
        group.id,
        {"name": "日常创作", "description": "", "services": ["frontend"]},
        service_ids={"backend", "frontend"},
    )
    assert updated.name == "日常创作"
    assert store.remove_service_references("frontend") == [group.id]
    assert store.list_groups() == []


def test_group_rejects_missing_services(tmp_path: Path) -> None:
    store = ServiceGroupStore(tmp_path / "service-groups.json")
    with pytest.raises(ServiceGroupError, match="不存在"):
        store.create_group(
            {"name": "Broken", "description": "", "services": ["missing"]},
            service_ids={"known"},
        )
