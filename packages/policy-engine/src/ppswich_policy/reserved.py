"""Reserved argument names and input schema validation.

Model-generated arguments may only carry business parameters. Anything that
could smuggle credentials, routing or identity is rejected before the
request ever reaches the executor (docs/05-SECURITY.md STRIDE table:
"任意 Header", "SSRF", "身份伪造").
"""

from __future__ import annotations

from typing import Any

import jsonschema
from ppswich_core.errors import ApiError, ErrorCode

RESERVED_ARGUMENT_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "secret",
        "secret_ref",
        "credential_ref",
        "credential",
        "password",
        "target_url",
        "url",
        "uri",
        "endpoint",
        "endpoint_id",
        "headers",
        "http_headers",
        "user_id",
        "principal_id",
        "tenant_id",
        "workspace_id",
        "agent_id",
        "profile_id",
        "connection_id",
        "impersonate",
        "as_user",
    }
)


def _check_keys(mapping: dict[str, Any], path: str) -> None:
    for key, value in mapping.items():
        lowered = str(key).strip().lower()
        if lowered in RESERVED_ARGUMENT_NAMES:
            raise ApiError(
                ErrorCode.RESERVED_ARGUMENT,
                f"Argument '{key}' is reserved and may not be supplied by a model",
                {"field": f"{path}{key}"},
            )
        if isinstance(value, dict):
            _check_keys(value, f"{path}{key}.")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    _check_keys(item, f"{path}{key}[{index}].")


def validate_arguments(arguments: dict[str, Any], input_schema: dict[str, Any]) -> None:
    """Validate model-supplied arguments against the published input schema.

    Two gates, both fail-closed:
    1. reserved names anywhere in the structure -> 422 RESERVED_ARGUMENT;
    2. JSON Schema validation (strict: additionalProperties false enforced
       by the published schema itself) -> 422 SCHEMA_VIOLATION.
    """
    if not isinstance(arguments, dict):
        raise ApiError(ErrorCode.SCHEMA_VIOLATION, "Arguments must be a JSON object")

    _check_keys(arguments, "")

    try:
        jsonschema.validate(instance=arguments, schema=input_schema)
    except jsonschema.ValidationError as exc:
        raise ApiError(
            ErrorCode.SCHEMA_VIOLATION,
            "Arguments do not match the capability input schema",
            {"json_path": exc.json_path},
        ) from exc
