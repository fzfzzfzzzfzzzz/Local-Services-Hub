from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx


class ProcessComposeError(RuntimeError):
    """Base error for Process Compose operations."""


class ControllerOffline(ProcessComposeError):
    """The Process Compose controller could not be reached."""


class ControllerAuthenticationError(ProcessComposeError):
    """The Process Compose token was rejected."""


class ProcessComposeAPIError(ProcessComposeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProcessComposeClient:
    def __init__(
        self,
        base_url: str,
        token_file: str | Path,
        *,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token_file = Path(token_file)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        try:
            token = self.token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ControllerAuthenticationError(
                f"Process Compose token file is unavailable: {self.token_file}"
            ) from exc
        if len(token) < 20:
            raise ControllerAuthenticationError(
                "Process Compose token must contain at least 20 characters"
            )
        return {"X-PC-Token-Key": token}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self._client.request(
                method,
                path,
                headers=self._headers(),
                json=json_body,
            )
        except ControllerAuthenticationError:
            raise
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ControllerOffline(
                f"Process Compose is unavailable at {self.base_url}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProcessComposeError(f"Process Compose request failed: {exc}") from exc

        if response.status_code == 401:
            raise ControllerAuthenticationError("Process Compose rejected the API token")
        if response.is_error:
            try:
                payload = response.json()
                message = payload.get("error") or payload.get("message") or response.text
            except ValueError:
                message = response.text
            raise ProcessComposeAPIError(
                response.status_code,
                str(message or f"Process Compose returned HTTP {response.status_code}"),
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ProcessComposeError("Process Compose returned invalid JSON") from exc

    async def is_online(self) -> bool:
        await self._request("GET", "/live")
        return True

    async def reload_configuration(self) -> Any:
        """Reload the same config files used to start Process Compose."""
        return await self._request("POST", "/project/configuration")

    async def list_processes(self) -> dict[str, dict[str, Any]]:
        payload = await self._request("GET", "/processes")
        items = payload.get("data", []) if isinstance(payload, dict) else []
        return {
            str(item["name"]): item
            for item in items
            if isinstance(item, dict) and item.get("name")
        }

    async def get_process(self, process_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/process/{quote(process_id, safe='')}")

    async def start_process(self, process_id: str) -> Any:
        return await self._request("POST", f"/process/start/{quote(process_id, safe='')}")

    async def stop_process(self, process_id: str) -> Any:
        return await self._request("PATCH", f"/process/stop/{quote(process_id, safe='')}")

    async def restart_process(self, process_id: str) -> Any:
        return await self._request(
            "POST", f"/process/restart/{quote(process_id, safe='')}"
        )

    async def get_logs(
        self,
        process_id: str,
        *,
        limit: int = 200,
        end_offset: int = 0,
    ) -> list[str]:
        safe_limit = min(max(int(limit), 1), 500)
        safe_offset = max(int(end_offset), 0)
        payload = await self._request(
            "GET",
            f"/process/logs/{quote(process_id, safe='')}/{safe_offset}/{safe_limit}",
        )
        logs = payload.get("logs", []) if isinstance(payload, dict) else []
        return [str(line) for line in logs]

    async def clear_logs(self, process_id: str) -> Any:
        return await self._request(
            "DELETE",
            f"/process/logs/{quote(process_id, safe='')}",
        )

    async def get_ports(self, process_id: str) -> dict[str, list[int]]:
        payload = await self._request(
            "GET", f"/process/ports/{quote(process_id, safe='')}"
        )
        if not isinstance(payload, dict):
            return {"tcp_ports": [], "udp_ports": []}
        return {
            "tcp_ports": [int(port) for port in payload.get("tcp_ports", [])],
            "udp_ports": [int(port) for port in payload.get("udp_ports", [])],
        }
