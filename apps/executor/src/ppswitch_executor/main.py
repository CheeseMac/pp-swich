"""Trusted executor service.

The only process that resolves credentials and opens outbound connections.
Internal API (docs/07-API-SPEC.md section 7):

- ``POST /internal/v1/execute`` - run one signed, pre-approved plan;
- ``POST /internal/v1/secrets/verify`` - FEAT-03 activation proof (postgres
  credentials also prove a real read-only connection to the approved target);
- ``POST /internal/v1/db/discover`` - FEAT-04 schema/view metadata discovery
  for an approved database target (names + types only);
- ``POST /internal/v1/admin/seeds`` - demo-only provisioning channel for
  the MockSecretProvider (models the out-of-band trusted channel that
  OpenBao provisioning would use in production);
- ``GET /healthz``.

Authentication: ``X-Internal-Token`` plus an HMAC signature over the plan.
Production replaces the shared dev token with workload identity / mTLS.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header
from ppswitch_core.entities import InvocationPlan
from ppswitch_core.enums import AuthScheme
from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_policy import validate_arguments
from ppswitch_secrets import (
    DispatchingSecretProvider,
    MockSecretProvider,
    SecretProvider,
    SecretProviderUnavailable,
)
from pydantic import BaseModel, Field

from ppswitch_executor.adapters.db_adapter import DbAdapter, discover_objects
from ppswitch_executor.adapters.db_adapter import verify_readonly_role as verify_db_role
from ppswitch_executor.adapters.http_adapter import HttpAdapter
from ppswitch_executor.adapters.mock_adapter import MockAdapter
from ppswitch_executor.claim_security import (
    ClaimSecurity,
    material_tag,
    tags_match,
    verify_attestation,
    verify_claim_ticket,
)
from ppswitch_executor.network_policy import check_outbound_target
from ppswitch_executor.plan_signing import NonceStore, verify_plan_signature
from ppswitch_executor.response_guard import project_response

DEFAULT_INTERNAL_TOKEN = "dev-internal-token-synthetic"  # noqa: S105 - synthetic dev value


class ExecutorSettings(BaseModel):
    internal_token: str = Field(
        default_factory=lambda: os.environ.get(
            "PP_SWITCH_INTERNAL_TOKEN", DEFAULT_INTERNAL_TOKEN
        )
    )
    # review PR-SEC-05: demo mode is opt-in only; production must never run
    # with mock providers, mock transports or the synthetic internal token
    demo_mode: bool = Field(
        default_factory=lambda: os.environ.get("PP_SWITCH_DEMO_MODE", "false").lower()
        in ("1", "true", "yes")
    )
    # review8 DEP-14-F1: when set, ALL upstream traffic flows through the
    # controlled egress gateway (CONNECT-only forward proxy) instead of a
    # direct pinning transport; application-layer policy still runs first
    egress_proxy_url: str = Field(
        default_factory=lambda: os.environ.get("PP_SWITCH_EGRESS_PROXY", "")
    )
    # SECRET-06 (docs/41): shared-keyring isolation. Personal mode (the
    # default, single-user trust domain) derives the pin key from the
    # internal token; shared mode REQUIRES explicit keys, the local
    # attestation directory and the control URL (revocation freshness)
    # and refuses to start without them.
    shared_keyring: bool = Field(
        default_factory=lambda: os.environ.get(
            "PP_SWITCH_SHARED_KEYRING", "false"
        ).lower() in ("1", "true", "yes")
    )
    pin_key: str = Field(
        default_factory=lambda: os.environ.get("PP_SWITCH_PIN_KEY", "")
    )
    attest_key: str = Field(
        default_factory=lambda: os.environ.get("PP_SWITCH_ATTEST_KEY", "")
    )
    # FIX-02: authorization signing keys, separate trust channels from the
    # transport token; mandatory (explicit) in shared mode
    claim_ticket_key: str = Field(
        default_factory=lambda: os.environ.get("PP_SWITCH_CLAIM_TICKET_KEY", "")
    )
    plan_signing_key: str = Field(
        default_factory=lambda: os.environ.get("PP_SWITCH_PLAN_SIGNING_KEY", "")
    )
    local_attest_dir: str = Field(
        default_factory=lambda: os.environ.get(
            "PP_SWITCH_LOCAL_ATTEST_DIR",
            str(Path.home() / ".ppswitch" / "attest"),
        )
    )
    # used for the pre-execution claim revocation-freshness reverse query
    control_url: str = Field(
        default_factory=lambda: os.environ.get("PP_SWITCH_CONTROL_URL", "")
    )


class ExecuteRequest(BaseModel):
    plan: dict[str, Any]
    signature: str = Field(min_length=64, max_length=64)


class SecretVerifyRequest(BaseModel):
    secret_ref: str = Field(min_length=1, max_length=512)
    auth_scheme: str = "api_key"
    # FEAT-04: for postgres credentials the control plane also sends the
    # APPROVED target coordinates so activation can prove a real read-only
    # connection (host/port/database from the approved db_target row)
    db_target: dict | None = None
    # SECRET-06: mandatory claim ticket (purpose=verify) for keyring://
    # refs - the executor never resolves keyring material on the internal
    # token alone (docs/41 errata 3)
    claim_ticket: str | None = None


class DbDiscoverRequest(BaseModel):
    """FEAT-04: metadata discovery for an approved db target.

    Carries the frozen non-secret coordinates (host/port/database/
    allowed_schemas) plus the keyring secret_ref; returns column names and
    types only - never data, never credentials.
    """

    secret_ref: str = Field(min_length=1, max_length=512)
    target: dict
    # SECRET-06: claim ticket (purpose=discover) required for keyring refs
    claim_ticket: str | None = None


class AttestRequest(BaseModel):
    """SECRET-06 shared-mode first-claim ownership proof request.

    The one-time pairing CODE arrives from a hidden prompt via the
    request BODY (never argv); the local nonce must be read from the
    executor user's OS-protected attest directory.
    """

    name: str = Field(min_length=1, max_length=64)
    pairing_code: str = Field(min_length=6, max_length=64)
    ticket: str = Field(min_length=10)
    local_nonce: str = Field(min_length=16, max_length=128)


class AttestVerifyRequest(BaseModel):
    attestation: str = Field(min_length=10)
    namespace_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    principal_id: str = Field(min_length=1, max_length=64)
    workspace_id: str = Field(min_length=1, max_length=64)
    proof_id: str = Field(min_length=1, max_length=32)
    pairing_code_hash: str = Field(min_length=64, max_length=64)

class SeedRequest(BaseModel):
    secret_ref: str = Field(min_length=1, max_length=512)
    material: dict[str, Any]
    auth_scheme: str = "api_key"


class ExecutorState:
    def __init__(self, settings: ExecutorSettings | None = None) -> None:
        self.settings = settings or ExecutorSettings()
        # FEAT-03: default resolves keyring:// refs from the OS secure
        # store and everything else from the mock provider (fail-closed
        # per provider; see packages/secret-provider/keyring.py)
        self.provider: SecretProvider = DispatchingSecretProvider()
        self.nonces = NonceStore()
        self.http_adapter = HttpAdapter(
            network_checker=check_outbound_target,
            egress_proxy_url=self.settings.egress_proxy_url,
        )
        self.mock_adapter = MockAdapter()
        self.db_adapter = DbAdapter()
        # SECRET-06: validates the shared-mode key config (fail-closed)
        self.claims = ClaimSecurity(
            internal_token=self.settings.internal_token,
            shared=self.settings.shared_keyring,
            pin_key=self.settings.pin_key,
            attest_key=self.settings.attest_key,
            claim_ticket_key=self.settings.claim_ticket_key,
            plan_signing_key=self.settings.plan_signing_key,
            local_attest_dir=self.settings.local_attest_dir,
            control_url=self.settings.control_url,
        )


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _validate_production_settings(settings: ExecutorSettings) -> None:
    if settings.demo_mode:
        return
    if settings.internal_token == DEFAULT_INTERNAL_TOKEN or not settings.internal_token:
        raise RuntimeError(
            "Refusing to start outside demo mode with the default synthetic "
            "internal token: set PP_SWITCH_INTERNAL_TOKEN via your deployment "
            "platform (review PR-SEC-05)."
        )


def create_app_from_env():
    """Zero-arg ASGI factory for `uvicorn --factory` deployments."""
    return create_app()


def create_app(
    settings: ExecutorSettings | None = None,
    *,
    provider: SecretProvider | None = None,
    network_checker=check_outbound_target,
) -> FastAPI:
    settings = settings or ExecutorSettings()
    _validate_production_settings(settings)
    state = ExecutorState(settings)
    if provider is not None:
        state.provider = provider

    app = FastAPI(title="pp switch executor", version="0.1.0", docs_url=None, redoc_url=None)

    def require_internal_token(x_internal_token: str) -> None:
        if x_internal_token != state.settings.internal_token:
            raise ApiError(ErrorCode.UNAUTHENTICATED, "Internal token missing or invalid")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "service": "executor", "demo_mode": state.settings.demo_mode}

    @app.post("/internal/v1/secrets/verify")
    def verify_secret(
        payload: SecretVerifyRequest, x_internal_token: str = Header(default="")
    ) -> dict:
        """FEAT-03: prove a secret_ref resolves before activating a connection.

        Returns ONLY resolution metadata (auth scheme + display fingerprint
        +, for keyring refs, the full SECRET-06 material tag) - never the
        material itself.

        FEAT-04: postgres credentials must additionally open the APPROVED
        target with a non-superuser role (a real read-only connection);
        anything less fails closed and the connection stays pending.

        SECRET-06: keyring:// refs require a purpose=verify claim ticket;
        the internal token alone never authorizes a material read.
        """
        require_internal_token(x_internal_token)
        ticket_context: dict | None = None
        if payload.secret_ref.startswith("keyring://"):
            if not payload.claim_ticket:
                raise ApiError(
                    ErrorCode.KEYRING_CLAIM_PROOF_INVALID,
                    "Keyring verification requires a claim ticket",
                )
            ticket_context = verify_claim_ticket(
                payload.claim_ticket, key=state.claims.claim_ticket_key,
                purpose="verify",
            )
            _name = payload.secret_ref[len("keyring://"):]
            if ticket_context["name"] != _name:
                raise ApiError(
                    ErrorCode.KEYRING_CLAIM_PROOF_INVALID,
                    "Claim ticket does not match the referenced keyring name",
                )
        try:
            credential = state.provider.resolve(payload.secret_ref)
        except SecretProviderUnavailable as exc:
            return {"ok": False, "reason": "unavailable", "detail": str(exc)[:200]}
        if payload.db_target is not None:
            # FEAT-04: a db_target in the request means the control plane
            # is activating a postgres connection - the credential must BE
            # postgres material and open the target with a read-only role
            if credential.auth_scheme != AuthScheme.POSTGRES:
                return {
                    "ok": False,
                    "reason": "credential_not_postgres",
                    "detail": "db_target verification requires postgres material",
                }
            role_ok, role_reason = verify_db_role(payload.db_target, credential)
            if not role_ok:
                return {"ok": False, "reason": role_reason}
        import hashlib
        import json as _json

        digest = hashlib.sha256(
            _json.dumps(credential.material, sort_keys=True).encode("utf-8")
        ).hexdigest()[:8]
        response: dict[str, Any] = {
            "ok": True,
            "auth_scheme": credential.auth_scheme.value,
            "fingerprint": digest,
        }
        if ticket_context is not None:
            # full HMAC tag over the canonical material; the control plane
            # stores it as the pin and freezes it into signed plans
            response["material_tag"] = material_tag(
                state.claims.pin_key,
                namespace_id=ticket_context["namespace_id"],
                name=ticket_context["name"],
                auth_scheme=credential.auth_scheme.value,
                material=credential.material,
            )
            response["claim"] = {
                "generation": ticket_context["generation"],
                "pin_version": ticket_context["pin_version"],
            }
        return response

    @app.post("/internal/v1/keyring/attest")
    def keyring_attest(
        payload: AttestRequest, x_internal_token: str = Header(default="")
    ) -> dict:
        """FIX-03: ownership attestations are NEVER minted over HTTP.

        The signing surface is the OS-protected local channel only
        (AF_UNIX socket in the executor user's 0700 attest directory on
        POSIX; loopback + protected port file on Windows) - see
        LocalAttestListener. Even a valid internal token plus a stolen
        nonce cannot mint an attestation here.
        """
        require_internal_token(x_internal_token)
        raise ApiError(ErrorCode.NOT_FOUND, "No such endpoint")

    @app.post("/internal/v1/keyring/attest/verify")
    def keyring_attest_verify(
        payload: AttestVerifyRequest, x_internal_token: str = Header(default="")
    ) -> dict:
        """Control-side redemption check: constant-time verify the full
        attestation binding (namespace/name/principal/workspace/proof/
        code-hash/purpose/expiry)."""
        require_internal_token(x_internal_token)
        if not state.claims.attest_key:
            raise ApiError(ErrorCode.NOT_FOUND, "No such endpoint")
        verify_attestation(
            state.claims.attest_key,
            payload.attestation,
            namespace_id=payload.namespace_id,
            name=payload.name,
            principal_id=payload.principal_id,
            workspace_id=payload.workspace_id,
            proof_id=payload.proof_id,
            pairing_code_hash=payload.pairing_code_hash,
        )
        return {"ok": True}

    @app.post("/internal/v1/db/discover")
    def db_discover(
        payload: DbDiscoverRequest, x_internal_token: str = Header(default="")
    ) -> dict:
        """FEAT-04: schema/view metadata discovery for an approved target.

        Trusted-side only: the schemas listed in the (control-approved)
        target gate the information_schema query, the read-only role gates
        visibility, and only column names/types leave this endpoint.

        SECRET-06: keyring refs additionally require a purpose=discover
        claim ticket; the internal token alone never authorizes a
        material read (docs/41 errata 3).
        """
        require_internal_token(x_internal_token)
        if payload.secret_ref.startswith("keyring://"):
            if not payload.claim_ticket:
                raise ApiError(
                    ErrorCode.KEYRING_CLAIM_PROOF_INVALID,
                    "Keyring discovery requires a claim ticket",
                )
            ticket = verify_claim_ticket(
                payload.claim_ticket, key=state.claims.claim_ticket_key,
                purpose="discover",
            )
            if ticket["name"] != payload.secret_ref[len("keyring://"):]:
                raise ApiError(
                    ErrorCode.KEYRING_CLAIM_PROOF_INVALID,
                    "Claim ticket does not match the referenced keyring name",
                )
            _require_fresh_claim(state, ticket)
        try:
            credential = state.provider.resolve(payload.secret_ref)
        except SecretProviderUnavailable as exc:
            raise ApiError(ErrorCode.SECRET_PROVIDER_UNAVAILABLE, str(exc)) from exc
        if credential.auth_scheme != AuthScheme.POSTGRES:
            raise ApiError(
                ErrorCode.BAD_REQUEST, "Database discovery requires a postgres credential"
            )
        return discover_objects(payload.target, credential)

    @app.post("/internal/v1/execute")
    def execute(
        payload: ExecuteRequest, x_internal_token: str = Header(default="")
    ) -> dict:
        require_internal_token(x_internal_token)
        if not verify_plan_signature(payload.plan, payload.signature,
            state.claims.plan_signing_key):
            raise ApiError(ErrorCode.INVALID_SIGNATURE, "Plan signature verification failed")

        try:
            plan = InvocationPlan.model_validate(payload.plan)
        except ValueError as exc:
            # malformed/tampered plan shape (e.g. db_query identifiers):
            # refuse with a clean code instead of an unhandled 500
            raise ApiError(
                ErrorCode.DATABASE_QUERY_REJECTED,
                "Plan payload failed structural validation",
            ) from exc

        if time.time() > plan.deadline_epoch_s:
            raise ApiError(ErrorCode.DEADLINE_EXCEEDED, "Plan deadline has passed")
        if not state.nonces.check_and_remember(plan.nonce, plan.deadline_epoch_s):
            raise ApiError(ErrorCode.REPLAY_DETECTED, "Plan nonce was already used")

        # re-validate exactly like the control plane did: the executor does
        # not trust internal traffic blindly (docs/02 section 8)
        validate_arguments(plan.arguments, plan.input_schema)

        if not state.settings.demo_mode and plan.secret_ref.startswith("mock://"):
            # PR-SEC-05: mock credentials must never execute outside demo
            raise ApiError(
                ErrorCode.SECRET_PROVIDER_UNAVAILABLE,
                "Mock credential refs are rejected outside demo mode",
            )

        # SECRET-06 (docs/41 section 3.1): the executor's FINAL check for
        # keyring material - a valid claim context in the signed plan, a
        # fresh revocation state, and a full material-tag match computed
        # over the just-resolved credential. Fail-closed on any gap.
        if plan.secret_ref.startswith("keyring://"):
            if plan.claim is None:
                raise ApiError(
                    ErrorCode.KEYRING_CLAIM_PROOF_INVALID,
                    "Keyring credentials require a claim context in the plan",
                )
            _require_fresh_claim(
                state,
                {
                    "namespace_id": plan.claim.namespace_id,
                    "name": plan.claim.name,
                    "generation": plan.claim.generation,
                    "pin_version": plan.claim.pin_version,
                },
            )

        try:
            credential = state.provider.resolve(plan.secret_ref)
        except SecretProviderUnavailable as exc:
            # fail closed: no .env fallback, ever (SEC-013)
            raise ApiError(ErrorCode.SECRET_PROVIDER_UNAVAILABLE, str(exc)) from exc

        if plan.claim is not None:
            actual_tag = material_tag(
                state.claims.pin_key,
                namespace_id=plan.claim.namespace_id,
                name=plan.claim.name,
                auth_scheme=credential.auth_scheme.value,
                material=credential.material,
            )
            if not tags_match(plan.claim.material_tag, actual_tag):
                raise ApiError(
                    ErrorCode.SECRET_FINGERPRINT_MISMATCH,
                    "Credential material changed after activation; re-activate to "
                    "confirm the rotation",
                )

        # FEAT-04: database plans run through the dedicated read-only
        # adapter; the HTTP endpoint/mock selection below never applies.
        # The signed plan froze the APPROVED target coordinates; the
        # adapter re-checks the schema allowlist and its own policy layer.
        if plan.db_query is not None:
            raw = state.db_adapter.execute(plan, credential)
            guarded = project_response(
                raw, plan.allow_fields, output_schema=plan.output_schema
            )
            return {
                "invocation_id": str(plan.invocation_id),
                "result": guarded.result,
                "dropped_fields": guarded.dropped_fields,
            }

        if plan.endpoint_base_url.startswith("mock://"):
            if not state.settings.demo_mode:
                raise ApiError(
                    ErrorCode.UPSTREAM_FAILURE,
                    "Mock transports are rejected outside demo mode",
                )
            adapter = state.mock_adapter
        else:
            # policy side effect: raises on violation. GATEWAY MODE uses the
            # URL-level check only - the isolated executor cannot resolve
            # external names; the egress gateway owns resolution + IP policy
            from ppswitch_executor.network_policy import check_url_policy

            if state.settings.egress_proxy_url:
                check_url_policy(
                    _join_url(plan.endpoint_base_url,
                              plan.operation.path_template),
                    plan.approved_hosts,
                )
            else:
                check = network_checker(
                    _join_url(plan.endpoint_base_url,
                              plan.operation.path_template),
                    plan.approved_hosts,
                )
                del check
            adapter = state.http_adapter  # type: ignore[assignment]

        raw = adapter.execute(plan, credential)
        guarded = project_response(
            raw, plan.allow_fields, output_schema=plan.output_schema
        )
        return {
            "invocation_id": str(plan.invocation_id),
            "result": guarded.result,
            "dropped_fields": guarded.dropped_fields,
        }

    @app.post("/internal/v1/admin/seeds")
    def seed(
        payload: SeedRequest, x_internal_token: str = Header(default="")
    ) -> dict:
        require_internal_token(x_internal_token)
        if not state.settings.demo_mode:
            raise ApiError(ErrorCode.NOT_FOUND, "No such endpoint")
        seed_target = state.provider
        if isinstance(seed_target, DispatchingSecretProvider):
            seed_target = seed_target.mock
        if not isinstance(seed_target, MockSecretProvider):
            raise ApiError(ErrorCode.FORBIDDEN, "Seeding only supported for the mock provider")
        seed_target.seed(
            payload.secret_ref,
            {"auth_scheme": payload.auth_scheme, "material": payload.material},
        )
        return {"status": "seeded", "secret_ref": payload.secret_ref}

    @app.exception_handler(ApiError)
    def api_error_handler(_request, exc: ApiError) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.http_status, content=error_body(exc.code, exc.message, exc.details)
        )

    return app


def _require_fresh_claim(state: ExecutorState, claim: dict) -> None:
    """SECRET-06 revocation freshness: compare the plan/ticket's frozen
    claim generation against the control plane's LIVE state.

    Runs whenever a control URL is configured (always in shared mode -
    startup refuses without it). No cross-request caching: every material
    read re-queries (docs/41 section 8.3).
    """
    if not state.claims.control_url:
        return  # personal mode without control wiring; generation checks
        # still happen control-side at plan build
    import httpx

    url = (
        f"{state.claims.control_url}/internal/v1/keyring-claims/state"
        f"?namespace_id={claim['namespace_id']}&name={claim['name']}"
    )
    try:
        response = httpx.get(
            url,
            headers={"X-Internal-Token": state.settings.internal_token},
            timeout=5.0,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        raise ApiError(
            ErrorCode.SECRET_PROVIDER_UNAVAILABLE,
            "Claim revocation state unavailable",
        ) from exc
    if response.status_code != 200:
        raise ApiError(
            ErrorCode.SECRET_PROVIDER_UNAVAILABLE,
            "Claim revocation state unavailable",
        )
    live = response.json()
    if (
        live.get("status") != "active"
        or live.get("generation") != claim["generation"]
        or live.get("pin_version") != claim["pin_version"]
    ):
        raise ApiError(
            ErrorCode.KEYRING_CLAIM_PROOF_INVALID,
            "Claim state changed (released, disputed or rotated); refusing",
        )


def _join_url(base_url: str, path_template: str) -> str:
    if "{" in path_template:
        # substitute a syntactically valid placeholder for the policy check
        path_template = path_template.replace("{id}", "0")
        path_template = path_template.replace("{", "").replace("}", "")
    return f"{base_url.rstrip('/')}{path_template}"


# NOTE: no module-level app instance - production startup must go through
# main()/create_app() so the PR-SEC-05 settings validation always runs.


def main() -> None:  # pragma: no cover - console entry point
    import uvicorn

    app = create_app(ExecutorSettings())
    port = int(os.environ.get("PP_SWITCH_EXECUTOR_PORT", "8912"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
