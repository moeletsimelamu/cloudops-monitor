"""Per-request plumbing: request IDs, the access log, security headers, unhandled errors, and the
host-name and cross-site guard."""

import logging
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cloudops.errors import error_response
from cloudops.logging_setup import request_id_var

access_logger = logging.getLogger("cloudops.access")
error_logger = logging.getLogger("cloudops.errors")

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# JSON API responses never need to load anything.
_API_CSP = "default-src 'none'; frame-ancestors 'none'"
# FastAPI's Swagger UI page loads its assets from jsDelivr and uses an inline bootstrap script.
_DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
)
# The single-page app. Angular adds component styles at runtime, hence 'unsafe-inline' for styles.
_APP_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'"
)

_COMMON_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _headers_for(path: str) -> dict[str, str]:
    headers = dict(_COMMON_HEADERS)
    if path.startswith("/api/docs"):
        headers["Content-Security-Policy"] = _DOCS_CSP
    elif path.startswith("/api/"):
        headers["Content-Security-Policy"] = _API_CSP
        headers["Cache-Control"] = "no-store"
    else:
        headers["Content-Security-Policy"] = _APP_CSP
    return headers


class RequestContextMiddleware:
    """Pure ASGI middleware, outermost in the application's own stack."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)

        path: str = scope["path"]
        method: str = scope["method"]
        extra_headers = _headers_for(path)
        extra_headers["X-Request-ID"] = request_id
        response_state = {"status": 0, "started": False, "logged": False}
        started_at = time.perf_counter()

        def log_access() -> None:
            if response_state["logged"]:
                return
            response_state["logged"] = True
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
            # Path only: query strings can carry filter text and are deliberately not logged.
            access_logger.info(
                "%s %s -> %s (%.1f ms)",
                method,
                path,
                response_state["status"],
                elapsed_ms,
                extra={
                    "fields": {
                        "event": "http.request",
                        "method": method,
                        "path": path,
                        "status_code": response_state["status"],
                        "duration_ms": elapsed_ms,
                    }
                },
            )

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_state["started"] = True
                response_state["status"] = message["status"]
                headers = MutableHeaders(scope=message)
                for name, value in extra_headers.items():
                    headers.setdefault(name, value)
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                log_access()

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception:
            error_logger.exception("Unhandled error during %s %s", method, path)
            if response_state["started"]:
                raise
            response = error_response(
                Request(scope),
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "Something went wrong on the server. Quote the request ID if you report it.",
            )
            await response(scope, receive, send_with_headers)
        finally:
            log_access()  # if the response never completed (e.g. the client disconnected)
            request_id_var.reset(token)


@dataclass(frozen=True)
class BodyLimit:
    max_bytes: int
    message: str


class BodySizeLimitMiddleware:
    """Refuses oversized request bodies on every path while they are being received.

    Without it, any client, signed in or not, could make the server hold a request body of any
    size in memory: the framework reads the whole body before authentication is checked.
    ``Content-Length`` is checked first; the body is also counted as it arrives, because the
    header can be missing (chunked encoding) or wrong.
    """

    def __init__(
        self, app: ASGIApp, *, default: BodyLimit, per_path: Mapping[str, BodyLimit]
    ) -> None:
        self.app = app
        self.default = default
        self.per_path = dict(per_path)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.per_path.get(scope["path"], self.default)

        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit.max_bytes:
            response = error_response(
                Request(scope),
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "payload_too_large",
                limit.message,
            )
            await response(scope, receive, send)
            return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit.max_bytes:
                    raise HTTPException(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, limit.message)
            return message

        await self.app(scope, counting_receive, send)


_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _host_name(host_header: str) -> str:
    """'localhost:8300' -> 'localhost', '[::1]:8300' -> '[::1]'."""
    host = host_header.strip().lower()
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class HostAndOriginGuardMiddleware:
    """Protects the API from other websites.

    - DNS rebinding: a hostile domain can re-point itself to 127.0.0.1 and then read this API as
      if it were same-origin. Requests are only served for configured host names.
    - Cross-site requests: unsafe methods are rejected when the browser reports a different
      origin. Together with the SameSite=Strict session cookie this is the CSRF defence. Requests
      without these headers (curl, scripts, applications shipping logs) are not browser
      cross-site requests and pass.
    """

    def __init__(self, app: ASGIApp, *, allowed_hosts: list[str]) -> None:
        self.app = app
        self.allow_any_host = "*" in allowed_hosts
        self.allowed_hosts = {h.strip().lower() for h in allowed_hosts}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host = headers.get("host", "")
        if not self.allow_any_host and _host_name(host) not in self.allowed_hosts:
            await self._reject(
                scope,
                receive,
                send,
                HTTPStatus.BAD_REQUEST,
                "invalid_host",
                "This server does not answer for that host name. Add it to "
                "CLOUDOPS_ALLOWED_HOSTS if it is yours.",
            )
            return
        if scope["method"] in _UNSAFE_METHODS and self._is_cross_site(headers, host):
            await self._reject(
                scope,
                receive,
                send,
                HTTPStatus.FORBIDDEN,
                "cross_origin_request",
                "Requests from other websites are not allowed.",
            )
            return
        await self.app(scope, receive, send)

    @staticmethod
    def _is_cross_site(headers: Headers, host: str) -> bool:
        fetch_site = headers.get("sec-fetch-site")
        if fetch_site is not None and fetch_site not in ("same-origin", "none"):
            return True
        origin = headers.get("origin")
        if origin is None:
            return False
        if origin == "null":  # sandboxed frames, file:// pages
            return True
        return urlsplit(origin).netloc.lower() != host.strip().lower()

    @staticmethod
    async def _reject(
        scope: Scope, receive: Receive, send: Send, status: int, code: str, message: str
    ) -> None:
        response = error_response(Request(scope), status, code, message)
        await response(scope, receive, send)
