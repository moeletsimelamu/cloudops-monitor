"""FastAPI dependencies shared by the routers."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from cloudops.auth import SESSION_COOKIE, find_session, tokens_match
from cloudops.config import Settings
from cloudops.errors import AuthenticationRequiredError


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


def is_operator(request: Request, settings: Settings, db: Session) -> bool:
    if settings.auth_mode == "disabled":
        return True
    expected = settings.operator_token.get_secret_value() if settings.operator_token else ""
    supplied = bearer_token(request)
    if supplied is not None:
        return bool(expected) and tokens_match(supplied, expected)
    cookie = request.cookies.get(SESSION_COOKIE)
    return cookie is not None and find_session(db, cookie) is not None


def require_operator(request: Request, settings: SettingsDep, db: SessionDep) -> None:
    if not is_operator(request, settings, db):
        raise AuthenticationRequiredError()


OperatorDep = Depends(require_operator)
