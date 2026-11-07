"""Sign-in and sign-out for the operator."""

import logging
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from cloudops.api.deps import (
    OperatorDep,
    SessionDep,
    SettingsDep,
    client_key,
    is_operator,
    operator_token,
    refuse_if_limited,
)
from cloudops.auth import (
    SESSION_COOKIE,
    FailedSignInLimiter,
    create_session,
    end_all_sessions,
    end_session,
    find_session,
    tokens_match,
)
from cloudops.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class SignInRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class SessionStatus(BaseModel):
    auth_mode: Literal["token", "disabled"]
    authenticated: bool
    expires_at: datetime | None = None


class SessionsEnded(BaseModel):
    ended: int


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/api", httponly=True, samesite="strict")


@router.get("/session", response_model=SessionStatus)
def session_status(request: Request, settings: SettingsDep, db: SessionDep) -> SessionStatus:
    """Whether the caller is signed in. Never requires authentication itself."""
    cookie = request.cookies.get(SESSION_COOKIE)
    token = operator_token(settings)
    found = find_session(db, cookie, token) if cookie and token else None
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
    refuse_if_limited(request)
    expected = settings.operator_token.get_secret_value()
    if not tokens_match(body.token, expected):
        limiter: FailedSignInLimiter = request.app.state.sign_in_limiter
        limiter.record_failure(client_key(request))
        raise AppError(
            "That token is not correct. Check CLOUDOPS_OPERATOR_TOKEN on the server.",
            code="invalid_token",
            status_code=HTTPStatus.UNAUTHORIZED,
        )

    lifetime = timedelta(hours=settings.session_hours)
    raw, expires_at = create_session(db, lifetime, expected)
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
def sign_out(request: Request, response: Response, settings: SettingsDep, db: SessionDep) -> None:
    cookie = request.cookies.get(SESSION_COOKIE)
    token = operator_token(settings)
    if cookie and token:
        end_session(db, cookie, token)
    _clear_cookie(response)


@router.delete("/sessions", response_model=SessionsEnded, dependencies=[OperatorDep])
def sign_out_everywhere(response: Response, db: SessionDep) -> SessionsEnded:
    """End every browser session, this one included. For a lost or shared device; rotating the
    operator token (and restarting) also ends them all, and stops bearer use of the old token."""
    ended = end_all_sessions(db)
    logger.warning(
        "Every operator session was ended (%d)",
        ended,
        extra={"fields": {"event": "auth.sessions_ended", "count": ended}},
    )
    _clear_cookie(response)
    return SessionsEnded(ended=ended)
