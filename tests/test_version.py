from __future__ import annotations

from pathlib import Path

from version import __version__


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_public_version_markers_match_runtime_version() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "service-hub" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    generated_header_source = (
        PROJECT_ROOT / "service-hub" / "services" / "config_generator.py"
    ).read_text(encoding="utf-8")

    assert __version__ == "1.0.0"
    assert readme.startswith("# Local Service Hub V1.0\n")
    assert "PERSONAL LOCAL SERVICE REGISTRY · V1.0" in index
    assert "__version__" in generated_header_source
