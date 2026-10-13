"""Standard response envelope and error handling (docs/07-API-SPEC.md section 2).

Success: {"data": ..., "request_id": "req_..."}
Failure: {"error": {"code", "message", "details"}, "request_id": "req_..."}
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from ppswich_core.errors import ApiError, ErrorCode
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


def new_request_id() -> str:
    return "req_" + uuid.uuid4().hex[:20]


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request.state.request_id = new_request_id()
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response


def ok(request: Request, data: Any, status_code: int = 200) -> JSONResponse:
    # Commit the request's session BEFORE building the response: FastAPI
    # runs yield-dependency exit code after the response is sent, which
    # would let an immediate follow-up request race the commit.
    session = getattr(request.state, "db_session", None)
    if session is not None:
        session.commit()
    return JSONResponse(
        status_code=status_code,
        content={"data": _jsonable(data), "request_id": request.state.request_id},
    )


def _jsonable(value: Any) -> Any:
    import datetime
    import uuid as uuid_module

    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (uuid_module.UUID, datetime.datetime)):
        return str(value)
    return value


def error_response(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "error": {"code": str(exc.code), "message": exc.message, "details": exc.details},
            "request_id": getattr(request.state, "request_id", new_request_id()),
        },
    )


def install_error_handlers(app) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        api_error = ApiError(ErrorCode.BAD_REQUEST, "Request payload is invalid")
        return error_response(request, api_error)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # safe constant message: no stack traces or internal details out
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "INTERNAL_ERROR", "message": "Unexpected server error"},
                "request_id": getattr(request.state, "request_id", new_request_id()),
            },
        )
