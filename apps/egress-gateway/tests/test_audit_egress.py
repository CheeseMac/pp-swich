"""Production-audit regression (review9 / Gate R1): AUD-EGRESS-01..03.

Covers the metadata hard-deny policy (including NON-link-local cloud
endpoints), the CONNECT port rule, workload authentication and quotas.
"""

from __future__ import annotations

import asyncio

import pytest


class TestAUDEGRESS01PermanentDenySet:
    def test_cloud_metadata_endpoints_denied_even_when_allowlisted(self):
        from ppswitch_egress.main import Deny, GatewayPolicy, validate_target

        policy = GatewayPolicy(allowlist={"metadata.evil.test", "100.100.100.200"})
        cases = {
            "metadata.evil.test": ["100.100.100.200"],   # Alibaba ECS IMDS
            "v6.evil.test": ["fd00:ec2::254"],            # AWS IMDS IPv6
            "ll.evil.test": ["169.254.169.254"],          # classic IMDS
            "unspec.evil.test": ["0.0.0.0"],              # unspecified  # noqa: S104
        }
        import ppswitch_egress.main as m

        original = m._resolve_all
        try:
            for host, answers in cases.items():
                m._resolve_all = lambda h, p, a=answers: a
                with pytest.raises(Deny):
                    validate_target(policy, host, 443)
        finally:
            m._resolve_all = original

    def test_executor_side_policy_matches(self):
        import ipaddress

        from ppswitch_executor.network_policy import _hard_denied, canonical_ip

        for text in ("100.100.100.200", "fd00:ec2::254", "::ffff:100.100.100.200"):
            assert _hard_denied(canonical_ip(ipaddress.ip_address(text)))
        # RFC1918 stays approval-gated (internal APIs are a use case)
        assert not _hard_denied(ipaddress.ip_address("10.1.2.3"))


class TestAUDEGRESS02PortRule:
    def test_bare_hostname_allows_443_only(self):
        from ppswitch_egress.main import GatewayPolicy

        policy = GatewayPolicy(allowlist={"api.example.com"})
        assert policy.allows("api.example.com", 443)
        assert not policy.allows("api.example.com", 8443)
        assert not policy.allows("api.example.com", 80)

    def test_explicit_host_port_authorizes_that_port(self):
        from ppswitch_egress.main import GatewayPolicy

        policy = GatewayPolicy(allowlist={"api.example.com:8443"})
        assert policy.allows("api.example.com", 8443)
        assert not policy.allows("api.example.com", 443)  # bare name absent

    def test_nonstandard_port_refused_end_to_end(self, tls_server):
        port, _ = tls_server
        from ppswitch_egress.main import GatewayPolicy, create_server

        async def scenario():
            # allowlist has ONLY the explicit port (NOT 443): a CONNECT
            # to 443 must fail even for the allowlisted host
            policy = GatewayPolicy(allowlist={f"127.0.0.1:{port}"})
            server = await asyncio.start_server(
                create_server(policy), "127.0.0.1", 0
            )
            gw = server.sockets[0].getsockname()[1]
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", gw)
                writer.write(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: x\r\n\r\n")
                await writer.drain()
                status = await reader.readline()
                # the host IS allowlisted but only for its explicit port
                assert b"403" in status, status
            finally:
                server.close()
                await server.wait_closed()

        asyncio.run(scenario())


class TestAUDEGRESS03AuthAndQuotas:
    def _server(self, policy):
        from ppswitch_egress.main import create_server

        async def factory():
            return await asyncio.start_server(create_server(policy), "127.0.0.1", 0)

        from threading import Event

        ready = Event()
        holder = {}

        def runner():
            async def serve():
                server = await factory()
                holder["port"] = server.sockets[0].getsockname()[1]
                ready.set()
                async with server:
                    await server.serve_forever()

            asyncio.run(serve())

        import threading

        threading.Thread(target=runner, daemon=True).start()
        assert ready.wait(10)
        return holder["port"]

    def test_unauthenticated_proxy_use_refused(self, tls_server):
        port, _ = tls_server
        from ppswitch_egress.main import GatewayPolicy

        gw = self._server(GatewayPolicy(
            allowlist={f"127.0.0.1:{port}"},
            proxy_token="secret-workload-token",
        ))

        async def scenario():
            reader, writer = await asyncio.open_connection("127.0.0.1", gw)
            writer.write(f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
            await writer.drain()
            status = await reader.readline()
            writer.close()
            return status

        assert b"407" in asyncio.run(scenario())

    def test_authenticated_proxy_use_succeeds(self, tls_server):
        import httpx

        port, _ = tls_server
        from ppswitch_egress.main import GatewayPolicy

        gw = self._server(GatewayPolicy(
            allowlist={f"127.0.0.1:{port}"},
            proxy_token="secret-workload-token",
        ))
        with httpx.Client(
            proxy=f"http://workload:secret-workload-token@127.0.0.1:{gw}",
            verify=False, trust_env=False, timeout=10.0,  # noqa: S501 - fixture
        ) as client:
            response = client.get(f"https://127.0.0.1:{port}/ok")
        assert response.status_code == 200

    def test_rate_quota_blocks_flooding(self, tls_server):
        _port, _ = tls_server
        from ppswitch_egress.main import GatewayPolicy

        gw = self._server(GatewayPolicy(allowlist={"unreachable.test"}))

        async def scenario():
            statuses = []
            for _ in range(70):  # above RATE_MAX_CONNECTS per window
                reader, writer = await asyncio.open_connection("127.0.0.1", gw)
                writer.write(b"CONNECT unreachable.test:443 HTTP/1.1\r\nHost: x\r\n\r\n")
                await writer.drain()
                statuses.append(await reader.readline())
                writer.close()
            return statuses

        statuses = asyncio.run(scenario())
        assert any(b"429" in s for s in statuses)
        # and every request still received a policy response (403/429)
        assert all(b"403" in s or b"429" in s for s in statuses)
