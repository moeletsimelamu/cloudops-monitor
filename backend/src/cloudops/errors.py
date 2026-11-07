"""Application errors and the uniform JSON error envelope.

Every error response has the shape ``{"error": {"code", "message", "request_id", "details"?}}``.
Messages are written for the person reading them and never contain stack traces or secrets.
"""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

# How long a client should wait before retrying when the database is busy or unavailable.
DATABASE_RETRY_AFTER_SECONDS = 5


class AppError(Exception):
    """An expected error with a stable code and a message that is safe to show to the user."""

    status_code: int = HTTPStatus.BAD_REQUEST
    code: str = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details
        self.headers = headers


class NotFoundError(AppError):
    status_code = HTTPStatus.NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "conflict"


class AuthenticationRequiredError(AppError):
    status_code = HTTPStatus.UNAUTHORIZED
    code = "authentication_required"

    def __init__(self, message: str = "Sign in to continue.") -> None:
        super().__init__(message, headers={"WWW-Authenticate": "Bearer"})


def request_id_of(request: Request) -> str:
    return str(request.scope.get("state", {}).get("request_id", "-"))


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request_id_of(request),
    }
    if details is not None:
        body["details"] = details
    return JSONResponse(status_code=status_code, content={"error": body}, headers=headers)


_STATUS_CODES = {
    HTTPStatus.NOT_FOUND: "not_found",
    HTTPStatus.METHOD_NOT_ALLOWED: "method_not_allowed",
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: "payload_too_large",
}


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    error = cast(AppError, exc)
    return error_response(
        request, error.status_code, error.code, error.message, error.details, error.headers
    )


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    error = cast(StarletteHTTPException, exc)
    status = HTTPStatus(error.status_code)
    code = _STATUS_CODES.get(status, status.phrase.lower().replace(" ", "_"))
    message = error.detail if isinstance(error.detail, str) else status.phrase
    return error_response(request, error.status_code, code, message, headers=error.headers)


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    error = cast(RequestValidationError, exc)
    # Only location and message are returned: the rejected input itself is not echoed back,
    # because it may be a secret (for example a mistyped token).
    details = [
        {"location": [str(part) for part in item.get("loc", ())], "message": item.get("msg", "")}
        for item in error.errors()
    ]
    return error_response(
        request,
        HTTPStatus.UNPROCESSABLE_ENTITY,
        "invalid_request",
        "The request is invalid. See details for the fields to correct.",
        details,
    )


def is_database_busy(error: OperationalError) -> bool:
    """SQLite's "database is locked" / "database table is locked": another connection held the
    write lock for longer than ``busy_timeout``. Retrying shortly is the right response."""
    text = str(error.orig).lower()
    return "locked" in text or "busy" in text


async def _handle_database_error(request: Request, exc: Exception) -> JSONResponse:
    """The database could not be used. Not the client's fault, and usually temporary: 503 with
    ``Retry-After``, and a message that says what happened instead of a generic server error."""
    error = cast(OperationalError, exc)
    headers = {"Retry-After": str(DATABASE_RETRY_AFTER_SECONDS)}
    if is_database_busy(error):
        # Expected under contention (a backup, a long write elsewhere): no traceback needed.
        logger.warning(
            "Database busy during %s %s",
            request.method,
            request.url.path,
            extra={"fields": {"event": "database.busy", "error": str(error.orig)}},
        )
        return error_response(
            request,
            HTTPStatus.SERVICE_UNAVAILABLE,
            "database_busy",
            "The database is busy with another write and nothing was changed. Try again in a "
            "few seconds.",
            headers=headers,
        )
    logger.error(
        "Database unavailable during %s %s",
        request.method,
        request.url.path,
        exc_info=error,
        extra={"fields": {"event": "database.unavailable"}},
    )
    return error_response(
        request,
        HTTPStatus.SERVICE_UNAVAILABLE,
        "database_unavailable",
        "The database cannot be used right now, so nothing was changed. The server's log says "
        "why; quote the request ID if you report it.",
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(OperationalError, _handle_database_error)
