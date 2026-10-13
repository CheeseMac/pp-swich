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
