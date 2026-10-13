import pytest
from ppswich_core.enums import (
    CapabilityStatus,
    ConnectionStatus,
    EndpointStatus,
    PrincipalKind,
    ProfileStatus,
    RiskLevel,
)
from ppswich_core.errors import ApiError, ErrorCode
from ppswich_policy import evaluate_invocation, validate_arguments


def _ok_kwargs(**overrides):
    base = dict(
        principal_kind=PrincipalKind.USER,
        capability_status=CapabilityStatus.PUBLISHED,
        agent_has_grant=False,
        connection_status=ConnectionStatus.ACTIVE,
        profile_status=ProfileStatus.ENABLED,
        endpoint_status=EndpointStatus.APPROVED,
        risk_level=RiskLevel.LOW,
    )
    base.update(overrides)
    return base


class TestEvaluateInvocation:
    def test_happy_path_low_risk(self) -> None:
        decision = evaluate_invocation(**_ok_kwargs())
        assert decision.allowed and not decision.approval_required

    def test_draft_capability_denied(self) -> None:
        decision = evaluate_invocation(**_ok_kwargs(capability_status=CapabilityStatus.DRAFT))
        assert not decision.allowed and decision.reason == "capability_not_published"

    def test_agent_without_grant_denied(self) -> None:
        decision = evaluate_invocation(
            **_ok_kwargs(principal_kind=PrincipalKind.AGENT, agent_has_grant=False)
        )
        assert not decision.allowed and decision.reason == "agent_missing_grant"

    def test_agent_with_valid_grant_allowed(self) -> None:
        decision = evaluate_invocation(
            **_ok_kwargs(principal_kind=PrincipalKind.AGENT, agent_has_grant=True)
        )
        assert decision.allowed

    def test_expired_grant_denied(self) -> None:
        decision = evaluate_invocation(
            **_ok_kwargs(
                principal_kind=PrincipalKind.AGENT,
                agent_has_grant=True,
                agent_grant_expired=True,
            )
        )
        assert not decision.allowed and decision.reason == "agent_grant_expired"

    def test_high_risk_requires_approval(self) -> None:
        decision = evaluate_invocation(**_ok_kwargs(risk_level=RiskLevel.HIGH))
        assert decision.allowed and decision.approval_required

    def test_endpoint_not_approved_denied(self) -> None:
        decision = evaluate_invocation(**_ok_kwargs(endpoint_status=EndpointStatus.PENDING))
        assert not decision.allowed and decision.reason == "endpoint_not_approved"

    def test_unknown_principal_denied(self) -> None:
        decision = evaluate_invocation(**_ok_kwargs(principal_kind=None))
        assert not decision.allowed


INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["customer_id"],
    "properties": {"customer_id": {"type": "string", "minLength": 1, "maxLength": 64}},
}


class TestValidateArguments:
    def test_valid_arguments_pass(self) -> None:
        validate_arguments({"customer_id": "C-1024"}, INPUT_SCHEMA)

    def test_reserved_authorization_rejected(self) -> None:
        with pytest.raises(ApiError) as excinfo:
            validate_arguments(
                {"customer_id": "C-1", "Authorization": "Bearer CANARY_NOT_REAL_ABC123"},
                INPUT_SCHEMA,
            )
        assert excinfo.value.code == ErrorCode.RESERVED_ARGUMENT
        assert excinfo.value.http_status == 422

    def test_reserved_target_url_rejected(self) -> None:
        with pytest.raises(ApiError) as excinfo:
            validate_arguments(
                {"customer_id": "C-1", "target_url": "http://169.254.169.254/latest/meta-data"},
                INPUT_SCHEMA,
            )
        assert excinfo.value.code == ErrorCode.RESERVED_ARGUMENT

    def test_reserved_nested_and_case_insensitive(self) -> None:
        with pytest.raises(ApiError):
            validate_arguments(
                {"customer_id": "C-1", "options": {"Api_Key": "x"}},
                INPUT_SCHEMA,
            )
        with pytest.raises(ApiError):
            validate_arguments(
                {"customer_id": "C-1", "items": [{"secret_ref": "x"}]},
                INPUT_SCHEMA,
            )

    def test_identity_fields_rejected(self) -> None:
        for field in ("user_id", "tenant_id", "workspace_id", "agent_id"):
            with pytest.raises(ApiError):
                validate_arguments({"customer_id": "C-1", field: "spoofed"}, INPUT_SCHEMA)

    def test_schema_violation_on_wrong_type(self) -> None:
        with pytest.raises(ApiError) as excinfo:
            validate_arguments({"customer_id": 1234}, INPUT_SCHEMA)
        assert excinfo.value.code == ErrorCode.SCHEMA_VIOLATION

    def test_schema_violation_on_unknown_property(self) -> None:
        with pytest.raises(ApiError):
            validate_arguments({"customer_id": "C-1", "extra": "nope"}, INPUT_SCHEMA)
