"""Domain enums. Values must stay stable: they are persisted and serialized."""

from enum import StrEnum


class WorkspaceKind(StrEnum):
    PERSONAL = "personal"
    ORGANIZATION = "organization"


class Visibility(StrEnum):
    PRIVATE = "private"
    TEAM = "team"
    ORGANIZATION = "organization"


class EndpointStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DISABLED = "disabled"


class OperationStatus(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    NEEDS_REVIEW = "needs_review"
    VERIFIED = "verified"
    REJECTED = "rejected"


class CapabilityStatus(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    VERIFIED = "verified"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class CapabilityVersionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class ConnectionStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ProfileStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class InvocationStatus(StrEnum):
    PENDING = "pending"
    APPROVAL_REQUIRED = "approval_required"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    UPSTREAM_TIMEOUT = "upstream_timeout"


class PrincipalKind(StrEnum):
    USER = "user"
    AGENT = "agent"
    SERVICE = "service"


class AuthScheme(StrEnum):
    NONE = "none"
    API_KEY = "api_key"
    BEARER = "bearer"
    OAUTH2 = "oauth2"
    BASIC = "basic"
