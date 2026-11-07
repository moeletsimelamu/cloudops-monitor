"""FastAPI dependencies shared by the routers."""

from collections.abc import Iterator
from http import HTTPStatus
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from cloudops.auth import SESSION_COOKIE, FailedSignInLimiter, find_session, tokens_match
from cloudops.config import Settings
from cloudops.errors import AppError, AuthenticationRequiredError


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session(request: Request) -> Iterator[Session]:
    """A database session per request, committed when the endpoint returns normally."""
    session: Session = request.app.state.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]


def bearer_token(request: Request) -> str | None:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def client_key(request: Request) -> str:
    """Who a failed attempt is counted against: the connecting address (or, behind a proxy
    listed in CLOUDOPS_TRUSTED_PROXIES, the address it reports)."""
    return request.client.host if request.client else "unknown"


def refuse_if_limited(request: Request) -> None:
    """429 while the caller has too many recent failed attempts at the operator token."""
    limiter: FailedSignInLimiter = request.app.state.sign_in_limiter
    wait = limiter.retry_after(client_key(request))
    if wait:
        raise AppError(
            f"Too many failed sign-in attempts. Try again in {wait} seconds.",
            code="too_many_attempts",
            status_code=HTTPStatus.TOO_MANY_REQUESTS,
            headers={"Retry-After": str(wait)},
        )


def operator_token(settings: Settings) -> str:
    return settings.operator_token.get_secret_value() if settings.operator_token else ""


def is_operator(request: Request, settings: Settings, db: Session) -> bool:
    if settings.auth_mode == "disabled":
        return True
    expected = operator_token(settings)
    if not expected:
        return False
    supplied = bearer_token(request)
    if supplied is not None:
        # A bearer token is a guess at the operator token like a sign-in is, so it shares the
        # sign-in's failure limit; otherwise the API would allow unlimited guessing.
        refuse_if_limited(request)
        if tokens_match(supplied, expected):
            return True
        request.app.state.sign_in_limiter.record_failure(client_key(request))
        return False
    cookie = request.cookies.get(SESSION_COOKIE)
    return cookie is not None and find_session(db, cookie, expected) is not None


def require_operator(request: Request, settings: SettingsDep, db: SessionDep) -> None:
    if not is_operator(request, settings, db):
        raise AuthenticationRequiredError()


OperatorDep = Depends(require_operator)
