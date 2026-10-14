"""Shared fixtures for egress-gateway tests: a real self-signed TLS origin."""

from __future__ import annotations

import asyncio
import ssl
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


def _self_signed_context() -> ssl.SSLContext:
    import subprocess

    with tempfile.TemporaryDirectory() as tmp:
        crt = f"{tmp}/test.crt"
        key = f"{tmp}/test.key"
        subprocess.run(  # noqa: S603 - fixed argv, test fixture
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",  # noqa: S607
             "-keyout", key, "-out", crt, "-days", "1",
             "-subj", "/CN=localhost"],
            check=True, capture_output=True,
        )
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(crt, key)
        return server_ctx


def start_background_server(coro_factory) -> int:
    """Run an asyncio server on a daemon thread; returns its port."""
    ready = threading.Event()
    holder: dict = {}

    def runner() -> None:
        async def serve() -> None:
            server = await coro_factory()
            holder["port"] = server.sockets[0].getsockname()[1]
            ready.set()
            async with server:
                await server.serve_forever()

        asyncio.run(serve())

    threading.Thread(target=runner, daemon=True).start()
    assert ready.wait(10.0), "background server did not start"
    return holder["port"]


@pytest.fixture(scope="session")
def tls_server():
    """A local HTTPS server with a self-signed cert."""
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            body = b'{"status": "ok"}'
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    try:
        httpd.socket = _self_signed_context().wrap_socket(
            httpd.socket, server_side=True
        )
    except Exception as exc:  # openssl missing on the host
        pytest.skip(f"openssl unavailable: {exc}")
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield port, hits
    httpd.shutdown()
