"""E2E helpers: start real uvicorn servers on ephemeral ports."""

from __future__ import annotations

import socket
import threading
import time
from typing import Any

import pytest
import uvicorn


class ServerHandle:
    def __init__(self, server: uvicorn.Server, thread: threading.Thread, port: int) -> None:
        self.server = server
        self.thread = thread
        self.port = port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_server(app: Any, *, name: str = "server") -> ServerHandle:
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name=name, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while time.time() < deadline:
        if server.started:
            return ServerHandle(server, thread, port)
        time.sleep(0.05)
    raise RuntimeError(f"{name} failed to start")


@pytest.fixture(scope="session")
def start_server_fixture():
    return start_server
