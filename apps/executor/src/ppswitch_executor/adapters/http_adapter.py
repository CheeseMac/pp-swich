"""HTTP adapter: execute an approved plan against the reviewed endpoint.

Hard rules (docs/05-SECURITY.md section 5, review PR-SEC-03):
- the URL is assembled from the plan's reviewed endpoint + path template,
  never from model arguments;
- substituted path values must be single safe URL segments (no separators,
  no ``..``, no percent tricks);
- the FINAL URL (with arguments substituted) is re-validated against the
  approved host list before the request is sent;
- redirects are never followed; timeouts are always set;
- the credential header is attached here and nowhere else;
- the response body is read streaming with a hard byte cap.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from ppswitch_core.entities import InvocationPlan
from ppswitch_core.enums import AuthScheme
from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_secrets import CredentialMaterial

from ppswitch_executor.network_policy import NetworkPolicyViolation, PinningNetworkBackend

REQUEST_TIMEOUT_S = 10.0
MAX_READ_BYTES = 8 * 1024 * 1024  # hard cap before the guard's own budget

# a path-segment value: unreserved chars only, bounded, no dot-dot tricks
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")


def _segment_value(placeholder: str, value: Any) -> str:
    text = str(value)
    if not SAFE_SEGMENT.match(text) or ".." in text:
        raise ApiError(
            ErrorCode.UPSTREAM_FAILURE,
            "Path parameter contains characters that are not allowed in a URL segment",
            {"placeholder": placeholder},
        )
    return text


class NetworkValidatingTransport(httpx.HTTPTransport):
    """Validates the target at request time AND pins the connected IP.

    Two layers (review2 P0-3 / SEC-034):

    1. ``handle_request`` re-runs the full policy check on the final URL
       (fail fast, before any connection attempt);
    2. the underlying ``httpcore`` pool uses a :class:`PinningNetworkBackend`
       that resolves, policy-validates and connects to a VALIDATED IP
       literal - there is no second DNS resolution between the check and
       the socket, so a rebinding answer cannot change the connected
       address. TLS SNI / certificate checks stay bound to the origin
       hostname.
    """

    def __init__(self, approved_hosts: list[str], checker) -> None:
        super().__init__()
        self._approved_hosts = approved_hosts
        self._checker = checker
        # swap the connection pool's backend for the pinning one; the pool
        # itself (keepalive, retries, TLS wrap by origin hostname) is reused
        self._pool._network_backend = PinningNetworkBackend(  # type: ignore[attr-defined]
            approved_hosts, checker
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        try:
            self._checker(str(request.url), self._approved_hosts)
        except ApiError as exc:
            raise NetworkPolicyViolation(exc.message) from exc
        return super().handle_request(request)


class HttpAdapter:
    def __init__(self, client: httpx.Client | None = None, network_checker=None) -> None:
        self._client = client
        self._network_checker = network_checker

    def _client_for(self, plan: InvocationPlan) -> httpx.Client:
        if self._client is not None:
            return self._client
        transport = NetworkValidatingTransport(
            plan.approved_hosts, self._network_checker
        )
        # trusted executor traffic never flows through ambient system
        # proxies: only approved hosts are contacted, directly
        return httpx.Client(
            transport=transport, follow_redirects=False,
            timeout=REQUEST_TIMEOUT_S, trust_env=False,
        )

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
            path = path[:start] + _segment_value(placeholder_name, value) + path[end + 1 :]
        base = plan.endpoint_base_url.rstrip("/")
        return f"{base}{path}"

    def _read_capped(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes(chunk_size=64 * 1024):
            total += len(chunk)
            if total > MAX_READ_BYTES:
                raise ApiError(
                    ErrorCode.UPSTREAM_FAILURE, "Upstream response too large to process"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def execute(self, plan: InvocationPlan, credential: CredentialMaterial) -> Any:
        url = self._build_url(plan)
        # PR-SEC-03: validate the FINAL url (arguments substituted), not just
        # the template - a hostile argument cannot redirect the request
        if self._network_checker is not None:
            self._network_checker(url, plan.approved_hosts)

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

        owns_client = self._client is None
        client = self._client_for(plan)
        try:
            try:
                with client.stream(
                    plan.operation.method,
                    url,
                    params=params or None,
                    headers=headers,
                    json=body,
                ) as response:
                    if response.status_code >= 400:
                        # never leak upstream bodies: safe constant only
                        raise ApiError(
                            ErrorCode.UPSTREAM_FAILURE,
                            "Upstream returned an error status",
                            {"status": response.status_code},
                        )
                    content_type = response.headers.get("content-type", "")
                    if "json" not in content_type:
                        raise ApiError(
                            ErrorCode.UPSTREAM_FAILURE, "Upstream returned non-JSON content"
                        )
                    content = self._read_capped(response)
            except httpx.TimeoutException as exc:
                raise ApiError(ErrorCode.UPSTREAM_TIMEOUT, "Upstream request timed out") from exc
            except httpx.HTTPError as exc:
                raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Upstream request failed") from exc
        finally:
            if owns_client:
                client.close()

        try:
            return json.loads(content)
        except (ValueError, TypeError) as exc:
            raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Upstream returned invalid JSON") from exc
