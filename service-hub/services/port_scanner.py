from __future__ import annotations

import socket
from typing import Iterable

from .service_store import RESERVED_PORTS


DEFAULT_PORT_START = 8700
DEFAULT_PORT_END = 8999


def is_port_listening(port: int, host: str = "127.0.0.1") -> bool:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.08)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def recommended_ports(
    registered_ports: Iterable[int],
    *,
    start: int = DEFAULT_PORT_START,
    end: int = DEFAULT_PORT_END,
    limit: int = 3,
) -> list[int]:
    excluded = {int(port) for port in registered_ports} | RESERVED_PORTS
    result: list[int] = []
    for port in range(max(1, start), min(65535, end) + 1):
        if port in excluded or is_port_listening(port):
            continue
        result.append(port)
        if len(result) >= max(0, limit):
            break
    return result

