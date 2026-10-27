"""Sign-in and sign-out for the operator."""

from datetime import datetime, timedelta
from http import HTTPStatus
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from cloudops.api.deps import SessionDep, SettingsDep, is_operator
from cloudops.auth import (
    SESSION_COOKIE,
    FailedSignInLimiter,
    create_session,
    end_session,
    find_session,
    tokens_match,
)
from cloudops.errors import AppError

router = APIRouter(prefix="/auth", tags=["auth"])


class SignInRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class SessionStatus(BaseModel):
    auth_mode: Literal["token", "disabled"]
    authenticated: bool
    expires_at: datetime | None = None


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/session", response_model=SessionStatus)
def session_status(request: Request, settings: SettingsDep, db: SessionDep) -> SessionStatus:
    """Whether the caller is signed in. Never requires authentication itself."""
    cookie = request.cookies.get(SESSION_COOKIE)
    found = find_session(db, cookie) if cookie else None
    return SessionStatus(
        auth_mode=settings.auth_mode,
        authenticated=is_operator(request, settings, db),
        expires_at=found.expires_at if found else None,
    )


@router.post("/session", response_model=SessionStatus, status_code=HTTPStatus.CREATED)
def sign_in(
    body: SignInRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    db: SessionDep,
) -> SessionStatus:
    if settings.auth_mode == "disabled" or settings.operator_token is None:
        raise AppError(
            "Authentication is disabled on this server, so there is nothing to sign in to.",
            code="auth_disabled",
            status_code=HTTPStatus.CONFLICT,
        )
    limiter: FailedSignInLimiter = request.app.state.sign_in_limiter
    client = _client_key(request)
    wait = limiter.retry_after(client)
    if wait:
        raise AppError(
            f"Too many failed sign-in attempts. Try again in {wait} seconds.",
            code="too_many_attempts",
            status_code=HTTPStatus.TOO_MANY_REQUESTS,
            headers={"Retry-After": str(wait)},
        )
    if not tokens_match(body.token, settings.operator_token.get_secret_value()):
        limiter.record_failure(client)
        raise AppError(
            "That token is not correct. Check CLOUDOPS_OPERATOR_TOKEN on the server.",
            code="invalid_token",
            status_code=HTTPStatus.UNAUTHORIZED,
        )

    lifetime = timedelta(hours=settings.session_hours)
    raw, expires_at = create_session(db, lifetime)
    response.set_cookie(
        SESSION_COOKIE,
        raw,
        max_age=int(lifetime.total_seconds()),
        path="/api",
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    return SessionStatus(auth_mode="token", authenticated=True, expires_at=expires_at)


@router.delete("/session", status_code=HTTPStatus.NO_CONTENT)
def sign_out(request: Request, response: Response, db: SessionDep) -> None:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        end_session(db, cookie)
    response.delete_cookie(SESSION_COOKIE, path="/api", httponly=True, samesite="strict")
