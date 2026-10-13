"""HTTP adapter: execute an approved plan against the reviewed endpoint.

Hard rules (docs/05-SECURITY.md section 5):
- the URL is assembled from the plan's reviewed endpoint + path template,
  never from model arguments;
- redirects are never followed;
- timeouts are always set;
- the credential header is attached here and nowhere else.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from ppswich_core.entities import InvocationPlan
from ppswich_core.enums import AuthScheme
from ppswich_core.errors import ApiError, ErrorCode
from ppswich_secrets import CredentialMaterial

REQUEST_TIMEOUT_S = 10.0
MAX_READ_BYTES = 8 * 1024 * 1024  # hard cap before the guard's own budget


class HttpAdapter:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def _client_or_default(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(follow_redirects=False, timeout=REQUEST_TIMEOUT_S)
        return self._client

    def _build_url(self, plan: InvocationPlan) -> str:
        path = plan.operation.path_template
        while "{" in path:
            start = path.index("{")
            end = path.index("}", start)
            placeholder_name = path[start + 1 : end]
            argument_name = plan.operation.path_map.get(placeholder_name, placeholder_name)
            value = plan.arguments.get(argument_name)
            if value is None:
                raise ApiError(
                    ErrorCode.UPSTREAM_FAILURE,
                    "Missing path parameter for approved operation",
                    {"placeholder": placeholder_name},
                )
            path = path[:start] + str(value) + path[end + 1 :]
        base = plan.endpoint_base_url.rstrip("/")
        return f"{base}{path}"

    def execute(self, plan: InvocationPlan, credential: CredentialMaterial) -> Any:
        url = self._build_url(plan)
        headers: dict[str, str] = {}
        if credential.auth_scheme == AuthScheme.API_KEY:
            headers["X-API-Key"] = str(credential.material.get("api_key", ""))
        elif credential.auth_scheme == AuthScheme.BEARER:
            headers["Authorization"] = f"Bearer {credential.material.get('token', '')}"
        elif credential.auth_scheme != AuthScheme.NONE:
            raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Unsupported auth scheme for adapter")

        params = {
            q_name: plan.arguments[arg_name]
            for q_name, arg_name in plan.operation.query_template.items()
            if arg_name in plan.arguments
        }
        body: dict[str, Any] | None = None
        if plan.operation.method in ("POST", "PUT", "PATCH"):
            body = {
                field: plan.arguments[arg_name]
                for field, arg_name in plan.operation.body_template.items()
                if arg_name in plan.arguments
            }

        try:
            response = self._client_or_default().request(
                plan.operation.method,
                url,
                params=params or None,
                headers=headers,
                json=body,
            )
        except httpx.TimeoutException as exc:
            raise ApiError(ErrorCode.UPSTREAM_TIMEOUT, "Upstream request timed out") from exc
        except httpx.HTTPError as exc:
            raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Upstream request failed") from exc

        if len(response.content) > MAX_READ_BYTES:
            raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Upstream response too large to process")
        if response.status_code >= 400:
            # never leak upstream bodies: safe constant only
            raise ApiError(
                ErrorCode.UPSTREAM_FAILURE,
                "Upstream returned an error status",
                {"status": response.status_code},
            )
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Upstream returned non-JSON content")
        try:
            return json.loads(response.content)
        except (ValueError, TypeError) as exc:
            raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Upstream returned invalid JSON") from exc
