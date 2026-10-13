"""Trusted executor service.

The only process that resolves credentials and opens outbound connections.
Internal API (docs/07-API-SPEC.md section 7):

- ``POST /internal/v1/execute`` - run one signed, pre-approved plan;
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
from typing import Any

from fastapi import FastAPI, Header
from ppswich_core.entities import InvocationPlan
from ppswich_core.errors import ApiError, ErrorCode
from ppswich_policy import validate_arguments
from ppswich_secrets import MockSecretProvider, SecretProvider, SecretProviderUnavailable
from pydantic import BaseModel, Field

from ppswich_executor.adapters.http_adapter import HttpAdapter
from ppswich_executor.adapters.mock_adapter import MockAdapter
from ppswich_executor.network_policy import check_outbound_target
from ppswich_executor.plan_signing import NonceStore, verify_plan_signature
from ppswich_executor.response_guard import project_response

DEFAULT_INTERNAL_TOKEN = "dev-internal-token-synthetic"  # noqa: S105 - synthetic dev value


class ExecutorSettings(BaseModel):
    internal_token: str = Field(
        default_factory=lambda: os.environ.get(
            "PP_SWICH_INTERNAL_TOKEN", DEFAULT_INTERNAL_TOKEN
        )
    )
    demo_mode: bool = Field(
        default_factory=lambda: os.environ.get("PP_SWICH_DEMO_MODE", "true").lower()
        in ("1", "true", "yes")
    )


class ExecuteRequest(BaseModel):
    plan: dict[str, Any]
    signature: str = Field(min_length=64, max_length=64)


class SeedRequest(BaseModel):
    secret_ref: str = Field(min_length=1, max_length=512)
    material: dict[str, Any]
    auth_scheme: str = "api_key"


class ExecutorState:
    def __init__(self, settings: ExecutorSettings | None = None) -> None:
        self.settings = settings or ExecutorSettings()
        self.provider: SecretProvider = MockSecretProvider()
        self.nonces = NonceStore()
        self.http_adapter = HttpAdapter()
        self.mock_adapter = MockAdapter()


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def create_app(
    settings: ExecutorSettings | None = None,
    *,
    provider: SecretProvider | None = None,
    network_checker=check_outbound_target,
) -> FastAPI:
    state = ExecutorState(settings)
    if provider is not None:
        state.provider = provider

    app = FastAPI(title="pp swich executor", version="0.1.0", docs_url=None, redoc_url=None)

    def require_internal_token(x_internal_token: str) -> None:
        if x_internal_token != state.settings.internal_token:
            raise ApiError(ErrorCode.UNAUTHENTICATED, "Internal token missing or invalid")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "service": "executor", "demo_mode": state.settings.demo_mode}

    @app.post("/internal/v1/execute")
    def execute(
        payload: ExecuteRequest, x_internal_token: str = Header(default="")
    ) -> dict:
        require_internal_token(x_internal_token)
        if not verify_plan_signature(payload.plan, payload.signature,
            state.settings.internal_token):
            raise ApiError(ErrorCode.INVALID_SIGNATURE, "Plan signature verification failed")

        plan = InvocationPlan.model_validate(payload.plan)

        if time.time() > plan.deadline_epoch_s:
            raise ApiError(ErrorCode.DEADLINE_EXCEEDED, "Plan deadline has passed")
        if not state.nonces.check_and_remember(plan.nonce, plan.deadline_epoch_s):
            raise ApiError(ErrorCode.REPLAY_DETECTED, "Plan nonce was already used")

        # re-validate exactly like the control plane did: the executor does
        # not trust internal traffic blindly (docs/02 section 8)
        validate_arguments(plan.arguments, plan.input_schema)

        if plan.endpoint_base_url.startswith("mock://"):
            adapter = state.mock_adapter
        else:
            check = network_checker(
                _join_url(plan.endpoint_base_url, plan.operation.path_template),
                plan.approved_hosts,
            )
            del check  # policy side effect: raises on violation
            adapter = state.http_adapter  # type: ignore[assignment]

        try:
            credential = state.provider.resolve(plan.secret_ref)
        except SecretProviderUnavailable as exc:
            # fail closed: no .env fallback, ever (SEC-013)
            raise ApiError(ErrorCode.SECRET_PROVIDER_UNAVAILABLE, str(exc)) from exc

        raw = adapter.execute(plan, credential)
        guarded = project_response(raw, plan.allow_fields)
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
        if not isinstance(state.provider, MockSecretProvider):
            raise ApiError(ErrorCode.FORBIDDEN, "Seeding only supported for the mock provider")
        state.provider.seed(
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


def _join_url(base_url: str, path_template: str) -> str:
    if "{" in path_template:
        # substitute a syntactically valid placeholder for the policy check
        path_template = path_template.replace("{id}", "0")
        path_template = path_template.replace("{", "").replace("}", "")
    return f"{base_url.rstrip('/')}{path_template}"


app = create_app()


def main() -> None:  # pragma: no cover - console entry point
    import uvicorn

    port = int(os.environ.get("PP_SWICH_EXECUTOR_PORT", "8912"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
