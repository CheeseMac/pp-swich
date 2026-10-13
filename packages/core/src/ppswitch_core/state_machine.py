"""Capability lifecycle state machine (docs/04-CAPABILITY-CONTRACT.md section 5).

    draft -> reviewed -> verified -> published -> deprecated -> disabled
                                                -> disabled
    draft/reviewed/verified -> rejected (modelled as `disabled` + reason)
"""

from ppswitch_core.enums import CapabilityStatus
from ppswitch_core.errors import ApiError, ErrorCode

ALLOWED_CAPABILITY_TRANSITIONS: dict[CapabilityStatus, frozenset[CapabilityStatus]] = {
    CapabilityStatus.DRAFT: frozenset(
        {CapabilityStatus.REVIEWED, CapabilityStatus.DISABLED}
    ),
    CapabilityStatus.REVIEWED: frozenset(
        {CapabilityStatus.DRAFT, CapabilityStatus.VERIFIED, CapabilityStatus.DISABLED}
    ),
    CapabilityStatus.VERIFIED: frozenset(
        {CapabilityStatus.DRAFT, CapabilityStatus.PUBLISHED, CapabilityStatus.DISABLED}
    ),
    CapabilityStatus.PUBLISHED: frozenset(
        {CapabilityStatus.DEPRECATED, CapabilityStatus.DISABLED}
    ),
    CapabilityStatus.DEPRECATED: frozenset({CapabilityStatus.DISABLED}),
    CapabilityStatus.DISABLED: frozenset({CapabilityStatus.DRAFT}),
}


def transition_capability(current: CapabilityStatus, target: CapabilityStatus) -> CapabilityStatus:
    if target not in ALLOWED_CAPABILITY_TRANSITIONS[current]:
        raise ApiError(
            ErrorCode.CONFLICT,
            f"Capability status transition {current} -> {target} is not allowed",
            {"current": str(current), "target": str(target)},
        )
    return target
