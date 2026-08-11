from __future__ import annotations

from pathlib import Path

import pytest

from services.registry import RegistryError, load_registry


VALID_REGISTRY = """
projects:
  sample:
    name: Sample
    description: Sample service
    category: office
    namespace: sample
    home_url: http://127.0.0.1:8123
    processes:
      - id: sample_web
        role: web
        port: 8123
        required: true
        health_url: http://localhost:8123/health
scenes:
  office:
    name: Office
    projects: [sample]
"""


def write_registry(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "registry.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_valid_registry(tmp_path: Path) -> None:
    registry = load_registry(write_registry(tmp_path, VALID_REGISTRY))
    assert registry.projects["sample"].processes[0].port == 8123
    assert registry.scenes["office"].projects == ("sample",)


@pytest.mark.parametrize(
    "replacement, message",
    [
        ("port: 8123", "port: 70000"),
        ("http://127.0.0.1:8123", "https://example.com"),
        ("projects: [sample]", "projects: [missing]"),
    ],
)
def test_rejects_invalid_registry(
    tmp_path: Path, replacement: str, message: str
) -> None:
    invalid = VALID_REGISTRY.replace(replacement, message)
    with pytest.raises(RegistryError):
        load_registry(write_registry(tmp_path, invalid))


def test_rejects_duplicate_process_ids(tmp_path: Path) -> None:
    duplicate = VALID_REGISTRY.replace(
        "scenes:\n",
        "  second:\n"
        "    name: Second\n"
        "    namespace: second\n"
        "    processes:\n"
        "      - id: sample_web\n"
        "        role: worker\n"
        "        required: true\n"
        "scenes:\n",
    )
    with pytest.raises(RegistryError, match="Duplicate process id"):
        load_registry(write_registry(tmp_path, duplicate))

