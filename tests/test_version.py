from __future__ import annotations

from pathlib import Path

from version import __version__


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_public_version_markers_match_runtime_version() -> None:
    expected = f"v{__version__}"
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "service-hub" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    generated_header_source = (
        PROJECT_ROOT / "service-hub" / "services" / "config_generator.py"
    ).read_text(encoding="utf-8")

    assert readme.startswith(f"# Local Service Hub {expected}\n")
    assert expected in index
    assert "__version__" in generated_header_source
