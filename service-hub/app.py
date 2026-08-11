from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import AsyncIterator, Literal
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from services.config_generator import ProcessComposeConfigGenerator
from services.hub import HubConflict, HubService
from services.process_compose import (
    ControllerAuthenticationError,
    ControllerOffline,
    ProcessComposeAPIError,
    ProcessComposeClient,
    ProcessComposeError,
)
from services.process_inspector import ProcessInspectionError
from services.runtime_store import LogArchiveStore, RunHistoryStore
from services.service_group_store import (
    ServiceGroupError,
    ServiceGroupNotFoundError,
    ServiceGroupStore,
)
from services.service_store import (
    CorruptStoreError,
    ServiceNotFoundError,
    ServiceStore,
    ServiceStoreError,
    ServiceValidationError,
)
from services.status_resolver import StatusResolver
from version import __version__


SERVICE_HUB_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SERVICE_HUB_DIR.parent
STATIC_DIR = SERVICE_HUB_DIR / "static"


def _resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


class RuntimeItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=64)
    port: int = Field(ge=1, le=65535)
    command: str = Field(min_length=1, max_length=8192)


class ServiceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    port: int
    working_dir: str = Field(min_length=1, max_length=2048)
    command: str = Field(min_length=1, max_length=8192)
    url: str | None = Field(default=None, max_length=4096)
    type: Literal["frontend", "backend", "fullstack", "worker", "plugin", "other"] = "other"
    note: str = Field(default="", max_length=4000)
    health_url: str | None = Field(default=None, max_length=4096)
    health_check_type: Literal["process", "tcp", "http"] | None = None
    health_expected_status: int = Field(default=200, ge=100, le=599)
    dependencies: list[str] = Field(default_factory=list)
    runtime_items: list[RuntimeItemInput] | None = Field(
        default=None,
        min_length=1,
        max_length=12,
    )
    enabled: bool = True


class TakeoverInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    pid: int | None = Field(default=None, ge=1)


class ServiceGroupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    services: list[str] = Field(min_length=1)


def create_app(
    *,
    store: ServiceStore | None = None,
    process_compose: ProcessComposeClient | None = None,
    generator: ProcessComposeConfigGenerator | None = None,
    status_resolver: StatusResolver | None = None,
    group_store: ServiceGroupStore | None = None,
    run_history: RunHistoryStore | None = None,
    log_archive: LogArchiveStore | None = None,
) -> FastAPI:
    owns_client = process_compose is None
    if store is None:
        store = ServiceStore(
            _resolve_project_path(os.environ.get("SERVICES_PATH", "services.json"))
        )
    if generator is None:
        generator = ProcessComposeConfigGenerator(
            _resolve_project_path(
                os.environ.get(
                    "PROCESS_COMPOSE_GENERATED_PATH",
                    "process-compose.generated.yaml",
                )
            )
        )
    if store.degraded_error is None or store.using_backup:
        generator.generate(store.list_services())
    if process_compose is None:
        token_file = _resolve_project_path(
            os.environ.get(
                "PROCESS_COMPOSE_TOKEN_FILE", "runtime/process-compose.token"
            )
        )
        process_compose = ProcessComposeClient(
            os.environ.get("PROCESS_COMPOSE_URL", "http://127.0.0.1:8751"),
            token_file,
        )
    if status_resolver is None:
        status_resolver = StatusResolver()
    if group_store is None:
        group_store = ServiceGroupStore(store.path.parent / "service-groups.json")
    if run_history is None:
        run_history = RunHistoryStore(store.path.parent / "runtime" / "service-runs.json")
    if log_archive is None:
        log_archive = LogArchiveStore(store.path.parent / "runtime" / "logs")

    hub = HubService(
        store,
        process_compose,
        generator,
        status_resolver,
        group_store,
        run_history,
        log_archive,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await hub.close()
        if owns_client:
            await process_compose.close()

    app = FastAPI(
        title="Local Service Hub",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.hub = hub
    app.state.instance_id = uuid4().hex

    @app.exception_handler(ServiceNotFoundError)
    async def not_found_handler(_: Request, exc: ServiceNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "not_found", "detail": str(exc)})

    @app.exception_handler(ServiceValidationError)
    async def validation_handler(_: Request, exc: ServiceValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "invalid_service", "detail": str(exc)})

    @app.exception_handler(ServiceGroupNotFoundError)
    async def group_not_found_handler(_: Request, exc: ServiceGroupNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "group_not_found", "detail": str(exc)})

    @app.exception_handler(ServiceGroupError)
    async def group_error_handler(_: Request, exc: ServiceGroupError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "invalid_group", "detail": str(exc)})

    @app.exception_handler(CorruptStoreError)
    async def corrupt_store_handler(_: Request, exc: CorruptStoreError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"error": "corrupt_store", "detail": str(exc)})

    @app.exception_handler(ServiceStoreError)
    async def store_handler(_: Request, exc: ServiceStoreError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"error": "store_error", "detail": str(exc)})

    @app.exception_handler(HubConflict)
    async def conflict_handler(_: Request, exc: HubConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": exc.code, "detail": str(exc), **exc.context},
        )

    @app.exception_handler(ProcessInspectionError)
    async def inspection_handler(_: Request, exc: ProcessInspectionError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": "inspection_error", "detail": str(exc)})

    @app.exception_handler(ControllerOffline)
    async def controller_offline_handler(_: Request, exc: ControllerOffline) -> JSONResponse:
        return JSONResponse(status_code=503, content={"error": "controller_offline", "detail": str(exc)})

    @app.exception_handler(ControllerAuthenticationError)
    async def controller_auth_handler(_: Request, exc: ControllerAuthenticationError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"error": "controller_authentication_error", "detail": str(exc)})

    @app.exception_handler(ProcessComposeAPIError)
    async def process_compose_api_handler(_: Request, exc: ProcessComposeAPIError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"error": "process_compose_api_error", "detail": str(exc)})

    @app.exception_handler(ProcessComposeError)
    async def process_compose_handler(_: Request, exc: ProcessComposeError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"error": "process_compose_error", "detail": str(exc)})

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "local-service-hub",
            "version": __version__,
            "instance_id": app.state.instance_id,
            "registered_services": len(store.list_services()),
            "store_degraded": store.degraded_error is not None,
        }

    @app.get("/api/ports/recommended")
    async def ports_recommended() -> dict[str, object]:
        return {"ports": await hub.recommended_ports()}

    @app.get("/api/services")
    async def list_services() -> dict[str, object]:
        return await hub.snapshot()

    @app.get("/api/groups")
    async def list_groups() -> dict[str, object]:
        return await hub.list_groups()

    @app.post("/api/groups", status_code=status.HTTP_201_CREATED)
    async def create_group(payload: ServiceGroupInput) -> dict[str, object]:
        return await hub.create_group(payload.model_dump())

    @app.put("/api/groups/{group_id}")
    async def update_group(
        group_id: str,
        payload: ServiceGroupInput,
    ) -> dict[str, object]:
        return await hub.update_group(group_id, payload.model_dump())

    @app.delete("/api/groups/{group_id}")
    async def delete_group(group_id: str) -> dict[str, object]:
        return await hub.delete_group(group_id)

    @app.post("/api/groups/{group_id}/start")
    async def start_group(group_id: str) -> dict[str, object]:
        return await hub.start_group(group_id)

    @app.post("/api/services", status_code=status.HTTP_201_CREATED)
    async def create_service(payload: ServiceInput) -> dict[str, object]:
        return await hub.create_service(payload.model_dump())

    @app.get("/api/services/{service_id}")
    async def get_service(service_id: str) -> dict[str, object]:
        return await hub.get_service_view(service_id)

    @app.put("/api/services/{service_id}")
    async def update_service(
        service_id: str,
        payload: ServiceInput,
        restart: bool | None = Query(default=None),
    ) -> dict[str, object]:
        return await hub.update_service(
            service_id,
            payload.model_dump(),
            restart=restart,
        )

    @app.delete("/api/services/{service_id}")
    async def delete_service(
        service_id: str,
        stop: bool = Query(default=False),
    ) -> dict[str, object]:
        return await hub.delete_service(service_id, stop=stop)

    @app.post("/api/services/{service_id}/start")
    async def start_service(service_id: str) -> dict[str, object]:
        return await hub.start_service(service_id)

    @app.post("/api/services/{service_id}/stop")
    async def stop_service(service_id: str) -> dict[str, object]:
        return await hub.stop_service(service_id)

    @app.post("/api/services/{service_id}/restart")
    async def restart_service(service_id: str) -> dict[str, object]:
        return await hub.restart_service(service_id)

    @app.post("/api/services/{service_id}/takeover")
    async def takeover_service(
        service_id: str,
        payload: TakeoverInput,
    ) -> dict[str, object]:
        return await hub.takeover_service(
            service_id,
            confirm=payload.confirm,
            pid=payload.pid,
        )

    @app.get("/api/services/{service_id}/logs")
    async def service_logs(
        service_id: str,
        limit: int = Query(default=200, ge=1, le=500),
    ) -> dict[str, object]:
        return await hub.get_logs(service_id, limit)

    @app.delete("/api/services/{service_id}/logs")
    async def clear_service_logs(service_id: str) -> dict[str, object]:
        return await hub.clear_logs(service_id)

    @app.post("/api/services/{service_id}/open-directory")
    async def open_service_directory(service_id: str) -> dict[str, object]:
        return await hub.open_service_directory(service_id)

    @app.post("/api/store/restore-backup")
    async def restore_backup() -> dict[str, object]:
        return await hub.restore_backup()

    @app.post("/api/hub/shutdown", status_code=status.HTTP_202_ACCEPTED)
    async def shutdown_hub(background_tasks: BackgroundTasks) -> dict[str, object]:
        await hub.prepare_hub_shutdown()
        background_tasks.add_task(hub.shutdown_hub)
        return {
            "accepted": True,
            "message": "Service Hub 正在关闭；业务服务将继续运行",
        }

    @app.post("/api/hub/restart", status_code=status.HTTP_202_ACCEPTED)
    async def restart_hub(background_tasks: BackgroundTasks) -> dict[str, object]:
        await hub.prepare_hub_restart()
        background_tasks.add_task(hub.restart_hub)
        return {
            "accepted": True,
            "instance_id": app.state.instance_id,
            "message": "Service Hub 正在重启；业务服务将继续运行",
        }

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
