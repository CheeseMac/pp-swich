"""Outbound network policy (SSRF defense).

Every execution target comes from a server-generated plan whose host must
appear in the endpoint's reviewed ``approved_hosts`` list. Resolved IPs are
additionally denied when they are loopback / private / link-local / cloud
metadata ranges - except for hosts that were explicitly approved by an
operator (the local demo mock CRM runs on loopback).

Model-supplied URLs never reach this layer: the control plane rejects
reserved arguments like ``target_url`` before a plan exists (SEC-007).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from ppswich_core.errors import ApiError, ErrorCode


@dataclass
class TargetCheck:
    url: str
    host: str
    port: int
    resolved_ips: list[str]


def _is_denied_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
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
    """Validate one outbound target; raise on policy violation.

    ``approved_hosts`` entries look like ``host`` or ``host:port`` and are
    matched case-insensitively against the URL host[:port].
    """
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

    resolved = resolver(host)
    if not resolved:
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Target host cannot be resolved")
    for ip_text in resolved:
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError as exc:
            raise ApiError(ErrorCode.UPSTREAM_FAILURE,
                "Target resolved to invalid address") from exc
        if _is_denied_ip(ip) and not explicitly_approved:
            raise ApiError(
                ErrorCode.UPSTREAM_FAILURE,
                "Target resolves to a denied address range",
                {"host": host, "reason": "private_or_metadata_range"},
            )

    return TargetCheck(url=url, host=host, port=port, resolved_ips=resolved)
