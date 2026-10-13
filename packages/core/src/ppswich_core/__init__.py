"""pp swich core domain contracts.

Pure Pydantic models, enums, typed errors and state machines shared by the
control plane, the executor and the SDKs. This package must never depend on
any app package or on persistence technology (see docs/08-REPOSITORY-STRUCTURE.md).
"""

from ppswich_core.entities import (
    ActiveBinding,
    AgentGrant,
    AuditEvent,
    Capability,
    CapabilityVersion,
    Connection,
    ConnectionPublic,
    Endpoint,
    Invocation,
    InvocationPlan,
    Observation,
    Operation,
    OperationVersion,
    Principal,
    PrincipalPublic,
    Profile,
    ProfilePublic,
    Service,
    Workspace,
    validate_profile_alignment,
)
from ppswich_core.enums import (
    AuthScheme,
    CapabilityStatus,
    ConnectionStatus,
    EndpointStatus,
    InvocationStatus,
    OperationStatus,
    PrincipalKind,
    ProfileStatus,
    RiskLevel,
    Visibility,
    WorkspaceKind,
)
from ppswich_core.errors import ApiError, ErrorCode
from ppswich_core.state_machine import (
    ALLOWED_CAPABILITY_TRANSITIONS,
    transition_capability,
)

__all__ = [
    "ALLOWED_CAPABILITY_TRANSITIONS",
    "ActiveBinding",
    "AgentGrant",
    "ApiError",
    "AuditEvent",
    "AuthScheme",
    "Capability",
    "CapabilityStatus",
    "CapabilityVersion",
    "Connection",
    "ConnectionPublic",
    "ConnectionStatus",
    "Endpoint",
    "EndpointStatus",
    "ErrorCode",
    "Invocation",
    "InvocationPlan",
    "InvocationStatus",
    "Observation",
    "Operation",
    "OperationStatus",
    "OperationVersion",
    "Principal",
    "PrincipalKind",
    "PrincipalPublic",
    "Profile",
    "ProfilePublic",
    "ProfileStatus",
    "RiskLevel",
    "Service",
    "Visibility",
    "Workspace",
    "WorkspaceKind",
    "transition_capability",
    "validate_profile_alignment",
]
