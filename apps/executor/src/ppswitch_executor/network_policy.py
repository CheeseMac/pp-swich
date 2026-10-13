"""Outbound network policy (SSRF defense, review PR-SEC-03).

Rules (all enforced together):

1. **Mandatory host whitelist**: every target host (or host:port) must be
   explicitly listed in the endpoint's reviewed ``approved_hosts``. Public
   hosts are no longer implicitly allowed.
2. **Non-overridable deny set**: link-local ranges (which host the cloud
   metadata services, e.g. 169.254.169.254) are always denied, even when
   someone mistakenly approves such a host.
3. Private/loopback/reserved addresses are denied unless the exact host is
   explicitly approved (the local demo mock CRM runs on loopback).
4. Every resolved IP is validated; ``ValidatingTransport`` re-resolves and
   re-validates at request time to pin the address actually connected
   (DNS rebinding mitigation, SEC-008).

Model-supplied URLs never reach this layer: the control plane rejects
reserved arguments like ``target_url`` before a plan exists (SEC-007).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpcore
from ppswitch_core.errors import ApiError, ErrorCode


@dataclass
class TargetCheck:
    url: str
    host: str
    port: int
    resolved_ips: list[str]


class NetworkPolicyViolation(Exception):
    """Raised by the validating transport at request time (fail closed)."""


def _hard_denied(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Ranges that may never be exempted by an approved-host entry."""
    return ip.is_link_local


def _soft_denied(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Target host cannot be resolved") from exc
    return sorted({str(info[4][0]) for info in infos})


def check_outbound_target(
    url: str, approved_hosts: list[str], *, resolver=_resolve
) -> TargetCheck:
    """Validate one outbound target; raise on any policy violation."""
    split = urlsplit(url)
    if split.scheme not in ("http", "https"):
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Target scheme not allowed")
    if split.username or split.password:
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Credentials in URL are not allowed")

    host = split.hostname or ""
    port = split.port or (443 if split.scheme == "https" else 80)
    if not host:
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Target host missing")

    normalized_approved = {h.strip().lower() for h in approved_hosts}
    host_key = host.lower()
    hostport_key = f"{host.lower()}:{port}"
    explicitly_approved = host_key in normalized_approved or hostport_key in normalized_approved
    # rule 1: the host must be explicitly reviewed - public or not
    if not explicitly_approved:
        raise ApiError(
            ErrorCode.UPSTREAM_FAILURE,
            "Target host is not in the approved endpoint host list",
            {"host": host, "reason": "host_not_approved"},
        )

    resolved = resolver(host)
    if not resolved:
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Target host cannot be resolved")
    for ip_text in resolved:
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError as exc:
            raise ApiError(
                ErrorCode.UPSTREAM_FAILURE, "Target resolved to invalid address"
            ) from exc
        if _hard_denied(ip):
            # rule 2: metadata/link-local can never be approved away
            raise ApiError(
                ErrorCode.UPSTREAM_FAILURE,
                "Target resolves to a permanently denied address range",
                {"host": host, "reason": "link_local_or_metadata"},
            )
        # rule 3: private ranges only pass because the host was explicitly
        # approved above; the approval itself is the review record

    return TargetCheck(url=url, host=host, port=port, resolved_ips=resolved)


class PinningNetworkBackend(httpcore.SyncBackend):
    """True connect-time IP pinning (review2 P0-3 / SEC-034).

    Installed as the ``httpcore`` network backend of the validating
    transport: ``connect_tcp`` resolves + policy-validates the host and
    then delegates to ``SyncBackend.connect_tcp`` on the VALIDATED IP
    literal. Because the address handed down is a literal, neither
    httpcore nor the OS performs a second DNS resolution - the IP actually
    connected is exactly the one the check produced. TLS SNI and
    certificate hostname verification remain bound to the original origin
    hostname (httpcore derives ``server_hostname`` from the request URL,
    not from the backend's connect target), so pinning does not weaken
    certificate validation.
    """

    def __init__(self, approved_hosts: list[str], checker=check_outbound_target) -> None:
        super().__init__()
        self._approved_hosts = approved_hosts
        self._checker = checker

    def _validated_ip(self, host: str, port: int) -> str:
        # httpcore passes IPv6 literals without brackets; rebuild a URL the
        # checker can parse. The scheme placeholder only matters for the
        # checker's scheme allowlist (http/https both pass); the real scheme
        # was validated on the actual request URL by the transport.
        if ":" in host and not host.startswith("["):
            target = f"https://[{host}]:{port}/"
        else:
            target = f"https://{host}:{port}/"
        from ppswitch_core.errors import ApiError as _ApiError

        try:
            check = self._checker(target, self._approved_hosts)
        except _ApiError as exc:
            # same failure mode as the transport-level check: fail closed,
            # surfaced as a policy violation rather than a transport error
            raise NetworkPolicyViolation(exc.message) from exc
        # prefer IPv4 when both families are returned (deterministic)
        ipv4 = [ip for ip in check.resolved_ips if "." in ip]
        return (ipv4 or check.resolved_ips)[0]

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout=None,
        local_address=None,
        socket_options=None,
    ):
        pinned_ip = self._validated_ip(host, port)
        # connect to the validated literal; no further DNS lookup happens
        return super().connect_tcp(
            pinned_ip, port, timeout=timeout,
            local_address=local_address, socket_options=socket_options,
        )
