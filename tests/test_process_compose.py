from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from services.process_compose import (
    ControllerAuthenticationError,
    ProcessComposeAPIError,
    ProcessComposeClient,
)


@pytest.mark.asyncio
async def test_client_uses_token_and_current_api_methods(tmp_path: Path) -> None:
    token = "a" * 32
    token_file = tmp_path / "token"
    token_file.write_text(token, encoding="utf-8")
    observed: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-PC-Token-Key"] == token
        observed.append((request.method, request.url.path))
        if request.url.path == "/processes":
            return httpx.Response(200, json={"data": [{"name": "demo"}]})
        if request.url.path.startswith("/process/logs"):
            return httpx.Response(200, json={"logs": ["one", "two"]})
        return httpx.Response(200, json={"name": "demo"})

    client = ProcessComposeClient(
        "http://127.0.0.1:8751",
        token_file,
        transport=httpx.MockTransport(handler),
    )
    try:
        assert "demo" in await client.list_processes()
        await client.start_process("demo")
        await client.stop_process("demo")
        await client.restart_process("demo")
        assert await client.get_logs("demo") == ["one", "two"]
        await client.clear_logs("demo")
    finally:
        await client.close()

    assert ("POST", "/process/start/demo") in observed
    assert ("PATCH", "/process/stop/demo") in observed
    assert ("POST", "/process/restart/demo") in observed
    assert ("DELETE", "/process/logs/demo") in observed


@pytest.mark.asyncio
async def test_client_rejects_short_token_before_request(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("short", encoding="utf-8")
    client = ProcessComposeClient(
        "http://127.0.0.1:8751",
        token_file,
        transport=httpx.MockTransport(lambda _: httpx.Response(200)),
    )
    try:
        with pytest.raises(ControllerAuthenticationError):
            await client.list_processes()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_client_surfaces_api_error(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("b" * 32, encoding="utf-8")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=json.dumps({"error": "bad process"}))

    client = ProcessComposeClient(
        "http://127.0.0.1:8751",
        token_file,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ProcessComposeAPIError, match="bad process"):
            await client.start_process("missing")
    finally:
        await client.close()
