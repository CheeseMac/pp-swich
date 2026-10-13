import uuid

import pytest
from ppswitch_core.entities import (
    ActiveBinding,
    Capability,
    CapabilityVersion,
    Connection,
    ConnectionPublic,
    Endpoint,
    Profile,
    validate_profile_alignment,
)
from pydantic import ValidationError


def _ids() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def test_capability_name_requires_namespace_dot_operation() -> None:
    with pytest.raises(ValidationError):
        Capability(workspace_id=uuid.uuid4(), name="get_customer", display_name="x",
                   owner_principal_id=None)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Capability(workspace_id=uuid.uuid4(), name="crm.GetCustomer", display_name="x",
                   owner_principal_id=None)  # type: ignore[arg-type]


def test_capability_version_semver_and_published_requires_timestamp() -> None:
    cap_id = uuid.uuid4()
    with pytest.raises(ValidationError):
        CapabilityVersion(capability_id=cap_id, operation_version_id=uuid.uuid4(),
                          version="1.0", content_hash="a" * 64)
    with pytest.raises(ValidationError):
        CapabilityVersion(
            capability_id=cap_id,
            operation_version_id=uuid.uuid4(),
            version="1.0.0",
            content_hash="a" * 64,
            status="published",
        )
    ok = CapabilityVersion(
        capability_id=cap_id,
        operation_version_id=uuid.uuid4(),
        version="1.0.0",
        content_hash="a" * 64,
        status="published",
        published_at="2026-10-13T00:00:00Z",
    )
    assert ok.version == "1.0.0"


def test_active_binding_context_key_whitelist() -> None:
    ws, principal, svc, profile = _ids()[0], _ids()[0], _ids()[0], _ids()[0]
    for good in ("personal", "agent:helper-01", "deployment:prod-eu"):
        binding = ActiveBinding(
            workspace_id=ws, principal_id=principal, service_id=svc,
            context_key=good, active_profile_id=profile, version=1,
            updated_by=principal,
        )
        assert binding.context_key == good
    with pytest.raises(ValidationError):
        ActiveBinding(
            workspace_id=ws, principal_id=principal, service_id=svc,
            context_key="agent", active_profile_id=profile, version=1,
            updated_by=principal,
        )


def test_profile_alignment_accepts_same_service() -> None:
    ws, principal = _ids()[0], _ids()[0]
    service_id, endpoint_id, connection_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    from ppswitch_core.entities import Service

    service = Service(id=service_id, workspace_id=ws, slug="crm", name="CRM",
                      owner_principal_id=principal)
    endpoint = Endpoint(id=endpoint_id, workspace_id=ws, service_id=service_id,
                        environment="demo", base_url="https://crm.example.test")
    connection = Connection(id=connection_id, workspace_id=ws, service_id=service_id,
                            owner_principal_id=principal, secret_ref="mock://x")
    profile = Profile(id=uuid.uuid4(), workspace_id=ws, service_id=service_id,
                      endpoint_id=endpoint_id, connection_id=connection_id,
                      owner_principal_id=principal, name="CRM Demo A")
    validate_profile_alignment(profile, endpoint, connection, service)


def test_profile_alignment_rejects_cross_service_connection() -> None:
    ws, principal = _ids()[0], _ids()[0]
    service_id = uuid.uuid4()
    from ppswitch_core.entities import Service

    service = Service(id=service_id, workspace_id=ws, slug="crm", name="CRM",
                      owner_principal_id=principal)
    endpoint = Endpoint(id=uuid.uuid4(), workspace_id=ws, service_id=service_id,
                        environment="demo", base_url="https://crm.example.test")
    # connection bound to a DIFFERENT service
    foreign_connection = Connection(id=uuid.uuid4(), workspace_id=ws,
                                    service_id=uuid.uuid4(),
                                    owner_principal_id=principal, secret_ref="mock://x")
    profile = Profile(id=uuid.uuid4(), workspace_id=ws, service_id=service_id,
                      endpoint_id=endpoint.id, connection_id=foreign_connection.id,
                      owner_principal_id=principal, name="bad")
    with pytest.raises(ValueError, match="different service"):
        validate_profile_alignment(profile, endpoint, foreign_connection, service)


def test_connection_public_projection_hides_secret_ref() -> None:
    ws, principal, svc = _ids()
    connection = Connection(id=uuid.uuid4(), workspace_id=ws, service_id=svc,
                            owner_principal_id=principal,
                            secret_ref="secretprovider://mock/conn/1")
    public = ConnectionPublic.from_connection(connection)
    dumped = public.model_dump()
    assert "secret_ref" not in dumped
    assert dumped["secret_configured"] is True


def test_entities_forbid_unknown_fields() -> None:
    ws, principal = _ids()[0], _ids()[0]
    with pytest.raises(ValidationError):
        Connection(id=uuid.uuid4(), workspace_id=ws, service_id=uuid.uuid4(),
                   owner_principal_id=principal, secret_ref="mock://x",
                   plaintext_api_key="CANARY_NOT_REAL_ABC123")  # type: ignore[call-arg]
