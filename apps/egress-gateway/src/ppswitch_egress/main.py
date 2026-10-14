"""Controlled egress gateway (PR-QA-06 / review8 DEP-14-F1).

A CONNECT-only forward proxy sitting between the isolated executor and
the outside world. The executor stays on a no-route network; ALL real
API traffic flows through this gateway, which enforces its OWN target
allowlist independently of (and in addition to) the executor's
application-layer policy:

- only CONNECT (HTTPS tunneling) is honored; plain-HTTP forwarding is
  refused, matching the https-only endpoint policy;
- the target host (or host:port) must be in the gateway's allowlist
  (EGRESS_ALLOWED_HOSTS / *_FILE), synced by the operator from the
  endpoints' approved_hosts;
- every resolved IP is canonicalized (IPv4-mapped / NAT64 wrappers are
  judged by the IPv4 they carry) and link-local / metadata ranges are
  HARD-denied even when allowlisted;
- the gateway connects to a validated IP literal, so its check and its
  connection cannot diverge (same pinning discipline as the executor);
- the TLS session is END-TO-END between executor and target - the
  gateway tunnels ciphertext and never sees credentials.

Allow/deny decisions are audit-logged; denials fail closed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from dataclasses import dataclass, field

from ppswitch_executor.network_policy import canonical_ip

logger = logging.getLogger("ppswitch.egress")

GATEWAY_PORT = 8914
_TUNNEL_TIMEOUT_S = 10.0
_MAX_HEADER_BYTES = 8192


@dataclass
class GatewayPolicy:
    allowlist: set[str] = field(default_factory=set)

    @classmethod
    def from_env(cls) -> GatewayPolicy:
        raw = os.environ.get("EGRESS_ALLOWED_HOSTS", "")
        file = os.environ.get("EGRESS_ALLOWED_HOSTS_FILE", "")
        entries = {h.strip().lower() for h in raw.split(",") if h.strip()}
        if file and os.path.exists(file):
            with open(file, encoding="utf-8") as handle:
                entries |= {
                    line.strip().lower()
                    for line in handle
                    if line.strip() and not line.startswith("#")
                }
        return cls(allowlist=entries)

    def allows(self, host: str, port: int) -> bool:
        host = host.lower().strip("[]")
        return host in self.allowlist or f"{host}:{port}" in self.allowlist


class Deny(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _resolve_all(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise Deny("target host cannot be resolved") from exc
    return sorted({str(info[4][0]) for info in infos})


def validate_target(policy: GatewayPolicy, host: str, port: int) -> str:
    """Full gateway-side check; returns the pinned IP to connect to."""
    if not policy.allows(host, port):
        raise Deny(f"host not in egress allowlist: {host}")
    pinned: str | None = None
    for ip_text in _resolve_all(host, port):
        try:
            ip = canonical_ip(ipaddress.ip_address(ip_text))
        except ValueError as exc:
            raise Deny("target resolved to an invalid address") from exc
        if ip.is_link_local:
            # metadata ranges are never exemptable, allowlist or not
            raise Deny("target resolves to a permanently denied range")
        pinned = str(ip)
    if pinned is None:
        raise Deny("target host cannot be resolved")
    return pinned


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(64 * 1024):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, TimeoutError, OSError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


async def handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    policy: GatewayPolicy,
) -> None:
    peer = writer.get_extra_info("peername")
    try:
        request_line = await asyncio.wait_for(reader.readline(), _TUNNEL_TIMEOUT_S)
        parts = request_line.split()
        if len(parts) < 2 or parts[0] != b"CONNECT":
            # plain-HTTP forwarding is refused: https-only policy
            writer.write(b"HTTP/1.1 403 Forbidden\r\n"
                         b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            logger.warning("deny peer=%s classification=plain_http", peer)
            return
        target = parts[1].decode("ascii", "replace")
        host, _, port_text = target.rpartition(":")
        port = int(port_text) if port_text.isdigit() else -1
        if not host or port <= 0:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return
        # drain remaining request headers
        total = len(request_line)
        while True:
            line = await asyncio.wait_for(reader.readline(), _TUNNEL_TIMEOUT_S)
            total += len(line)
            if total > _MAX_HEADER_BYTES:
                raise Deny("request headers too large")
            if line in (b"\r\n", b"\n", b""):
                break

        try:
            pinned_ip = await asyncio.to_thread(validate_target, policy, host, port)
        except Deny as deny:
            writer.write(b"HTTP/1.1 403 Forbidden\r\n"
                         b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            logger.warning(
                "deny peer=%s target=%s:%s classification=%s",
                peer, host, port, deny.reason,
            )
            return

        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(pinned_ip, port), _TUNNEL_TIMEOUT_S
        )
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        logger.info("allow peer=%s target=%s:%s via=%s", peer, host, port, pinned_ip)
        await asyncio.gather(
            _pipe(reader, upstream_writer),
            _pipe(upstream_reader, writer),
        )
    except TimeoutError:
        try:
            writer.write(b"HTTP/1.1 504 Gateway Timeout\r\nConnection: close\r\n\r\n")
            await writer.drain()
        except OSError:
            pass
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


def create_server(policy: GatewayPolicy | None = None):
    policy = policy or GatewayPolicy.from_env()

    async def _client_connected(reader, writer):
        await handle_connection(reader, writer, policy)

    return _client_connected


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    policy = GatewayPolicy.from_env()
    if not policy.allowlist:
        logger.warning("starting with an EMPTY allowlist - everything is denied")

    async def _serve() -> None:
        server = await asyncio.start_server(
            create_server(policy), "0.0.0.0", GATEWAY_PORT  # noqa: S104 - container entrypoint
        )
        logger.info("egress gateway listening on :%d (%d allowed hosts)",
                    GATEWAY_PORT, len(policy.allowlist))
        async with server:
            await server.serve_forever()

    asyncio.run(_serve())


if __name__ == "__main__":
    main()
