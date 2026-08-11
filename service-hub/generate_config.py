from __future__ import annotations

from pathlib import Path

from services.config_generator import ProcessComposeConfigGenerator
from services.service_store import ServiceStore


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    store = ServiceStore(PROJECT_ROOT / "services.json")
    if store.degraded_error and not store.using_backup:
        # Preserve the previous valid generated file so Service Hub can start and
        # present the explicit recovery UI.
        print(f"Warning: {store.degraded_error}")
        return
    ProcessComposeConfigGenerator(
        PROJECT_ROOT / "process-compose.generated.yaml"
    ).generate(store.list_services())


if __name__ == "__main__":
    main()
