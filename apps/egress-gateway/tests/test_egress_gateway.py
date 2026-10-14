"""Egress gateway tests (review8 DEP-14-F1).

A real TLS server + the CONNECT-only gateway: the full tunnel path runs
locally without docker. Verifies allow/deny, hard-denied metadata
ranges, plain-HTTP refusal and end-to-end ciphertext tunneling.
"""

from __future__ import annotations

import asyncio
import os
import ssl
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
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


def _start_background_server(coro_factory) -> tuple[int, threading.Event]:
    """Run an asyncio server on a daemon thread; returns (port, stop)."""
    ready = threading.Event()
    holder: dict = {}

    def runner() -> None:
        async def serve() -> None:
            server = await coro_factory()
            holder["server"] = server
            holder["port"] = server.sockets[0].getsockname()[1]
            ready.set()
            async with server:
                await server.serve_forever()

        asyncio.run(serve())

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    assert ready.wait(10.0), "background server did not start"
    return holder["port"], holder["server"]


@pytest.fixture(scope="module")
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


@pytest.fixture()
def gateway(tls_server):
    from ppswitch_egress.main import GatewayPolicy, create_server

    port, _ = tls_server
    policy = GatewayPolicy(allowlist={f"127.0.0.1:{port}"})

    async def factory():
        return await asyncio.start_server(create_server(policy), "127.0.0.1", 0)

    gw_port, _server = _start_background_server(factory)
    return gw_port, port


class TestEgressGateway:
    def test_allowed_target_tunnels_end_to_end_tls(self, gateway, tls_server):
        gw_port, target_port = gateway
        _, hits = tls_server
        with httpx.Client(
            proxy=f"http://127.0.0.1:{gw_port}",
            verify=False, trust_env=False, timeout=10.0,  # noqa: S501 - self-signed fixture
        ) as client:
            response = client.get(f"https://127.0.0.1:{target_port}/ping")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert "/ping" in hits  # the tunnel actually reached the origin

    def test_unapproved_target_denied(self, gateway):
        gw_port, _ = gateway

        async def scenario():
            reader, writer = await asyncio.open_connection("127.0.0.1", gw_port)
            writer.write(b"CONNECT 10.9.9.9:443 HTTP/1.1\r\nHost: 10.9.9.9\r\n\r\n")
            await writer.drain()
            status = await reader.readline()
            writer.close()
            return status

        assert b"403" in asyncio.run(scenario())

    def test_plain_http_forwarding_refused(self, gateway):
        gw_port, _ = gateway

        async def scenario():
            reader, writer = await asyncio.open_connection("127.0.0.1", gw_port)
            writer.write(b"GET http://127.0.0.1/x HTTP/1.1\r\nHost: x\r\n\r\n")
            await writer.drain()
            status = await reader.readline()
            writer.close()
            return status

        assert b"403" in asyncio.run(scenario())

    def test_allowlisted_metadata_range_still_hard_denied(self, monkeypatch):
        from ppswitch_egress.main import Deny, GatewayPolicy, validate_target

        policy = GatewayPolicy(allowlist={"metadata.evil.test"})
        monkeypatch.setattr(
            "ppswitch_egress.main._resolve_all",
            lambda host, port: ["169.254.169.254"],
        )
        with pytest.raises(Deny) as excinfo:
            validate_target(policy, "metadata.evil.test", 443)
        assert "permanently denied" in excinfo.value.reason

    def test_nat64_wrapper_hard_denied(self, monkeypatch):
        from ppswitch_egress.main import Deny, GatewayPolicy, validate_target

        policy = GatewayPolicy(allowlist={"nat64.evil.test"})
        monkeypatch.setattr(
            "ppswitch_egress.main._resolve_all",
            lambda host, port: ["64:ff9b::a9fe:a9fe"],
        )
        with pytest.raises(Deny):
            validate_target(policy, "nat64.evil.test", 443)

    def test_allowlist_from_file(self, tmp_path):
        from ppswitch_egress.main import GatewayPolicy

        listing = tmp_path / "allow.txt"
        listing.write_text(
            "# comment\napi.one.test\napi.two.test:8443\n", encoding="utf-8"
        )
        os.environ["EGRESS_ALLOWED_HOSTS_FILE"] = str(listing)
        try:
            policy = GatewayPolicy.from_env()
            assert policy.allows("api.one.test", 443)
            assert policy.allows("api.two.test", 8443)
            assert not policy.allows("api.three.test", 443)
        finally:
            del os.environ["EGRESS_ALLOWED_HOSTS_FILE"]

    def test_executor_gateway_mode_calls_outbound(
        self, gateway, tls_server, monkeypatch
    ):
        """Full executor path in gateway mode: plan -> checker -> proxy
        transport -> gateway -> TLS origin (the DEP-14-F1 closure)."""
        import uuid

        from ppswitch_core.entities import InvocationPlan, InvocationPlanOperation
        from ppswitch_executor.adapters.http_adapter import HttpAdapter
        from ppswitch_executor.network_policy import check_outbound_target
        from ppswitch_secrets import CredentialMaterial

        gw_port, target_port = gateway
        adapter = HttpAdapter(
            network_checker=check_outbound_target,
            egress_proxy_url=f"http://127.0.0.1:{gw_port}",
        )
        plan = InvocationPlan(
            invocation_id=uuid.uuid4(),
            capability_name="t.get", capability_version="1.0.0",
            capability_version_id=uuid.uuid4(),
            operation=InvocationPlanOperation(
                method="GET", path_template="/ping",
                path_map={}, query_template={}, body_template={},
            ),
            endpoint_base_url=f"https://127.0.0.1:{target_port}",
            approved_hosts=[f"127.0.0.1:{target_port}"],
            auth_scheme="none",
            secret_ref="mock://connections/x",
            allow_fields=["status"],
            input_schema={}, output_schema={}, arguments={},
            workspace_id=uuid.uuid4(), principal_id=uuid.uuid4(),
            agent_id=None, risk_level="low", policy_version="p",
            arguments_digest="d", idempotency_key=None,
            deadline_epoch_s=0.0, nonce="nonce-1234567890",
        )
        # TLS verification against a self-signed test cert: disabled for
        # this test only via an unverified client injected below
        import httpx as _httpx

        unverified = _httpx.Client(
            proxy=f"http://127.0.0.1:{gw_port}",
            verify=False, trust_env=False, timeout=10.0,  # noqa: S501 - fixture
        )
        adapter._client = unverified  # inject transport for cert-less test
        result = adapter.execute(
            plan,
            CredentialMaterial(auth_scheme="none", material={}),
        )
        assert result == {"status": "ok"}
