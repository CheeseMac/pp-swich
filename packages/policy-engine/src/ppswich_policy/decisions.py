"""Invocation authorization decisions.

The engine is given *server-verified facts only* and answers allow / deny /
approval-required. Unknown or missing facts always deny (fail-closed,
docs/05-SECURITY.md section 2).
"""

from __future__ import annotations

from dataclasses import dataclass

from ppswich_core.enums import (
    CapabilityStatus,
    ConnectionStatus,
    EndpointStatus,
    PrincipalKind,
    ProfileStatus,
    RiskLevel,
)

_HIGH_RISK = {RiskLevel.HIGH, RiskLevel.CRITICAL}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    approval_required: bool = False


def evaluate_invocation(
    *,
    principal_kind: PrincipalKind | None,
    capability_status: CapabilityStatus | None,
    agent_has_grant: bool,
    agent_grant_expired: bool = False,
    connection_status: ConnectionStatus | None,
    profile_status: ProfileStatus | None,
    endpoint_status: EndpointStatus | None,
    risk_level: RiskLevel = RiskLevel.LOW,
) -> PolicyDecision:
    if capability_status != CapabilityStatus.PUBLISHED:
        return PolicyDecision(False, "capability_not_published")
    if principal_kind is None:
        return PolicyDecision(False, "unknown_principal_kind")
    if principal_kind == PrincipalKind.AGENT:
        if not agent_has_grant:
            return PolicyDecision(False, "agent_missing_grant")
        if agent_grant_expired:
            return PolicyDecision(False, "agent_grant_expired")
    elif principal_kind not in (PrincipalKind.USER, PrincipalKind.SERVICE):
        return PolicyDecision(False, "unsupported_principal_kind")
    if connection_status != ConnectionStatus.ACTIVE:
        return PolicyDecision(False, "connection_not_active")
    if profile_status != ProfileStatus.ENABLED:
        return PolicyDecision(False, "profile_not_enabled")
    if endpoint_status != EndpointStatus.APPROVED:
        return PolicyDecision(False, "endpoint_not_approved")
    return PolicyDecision(True, "ok", approval_required=risk_level in _HIGH_RISK)
