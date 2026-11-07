"""Single-operator authentication.

The operator token (CLOUDOPS_OPERATOR_TOKEN) is exchanged for a random session ID that the
browser keeps in an HttpOnly, SameSite=Strict cookie. Scripts may send the operator token as a
bearer token instead. See docs/SECURITY.md §4.

The database stores an HMAC of each session ID keyed with the operator token, never the ID itself
and nothing derived from the token alone. A session therefore only works while the token it was
signed in with is configured: rotating CLOUDOPS_OPERATOR_TOKEN and restarting ends every session,
with nothing to remember. Ending them all without rotating is :func:`end_all_sessions`.
"""

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from cloudops.db.models import OperatorSession
from cloudops.db.types import utc_now

SESSION_COOKIE = "cloudops_session"


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def tokens_match(supplied: str, expected: str) -> bool:
    """Constant-time comparison (of digests, so the token length is not revealed either)."""
    return hmac.compare_digest(hash_secret(supplied), hash_secret(expected))


def session_digest(raw: str, operator_token: str) -> str:
    """What is stored for a session ID: an HMAC keyed with the operator token in use."""
    return hmac.new(operator_token.encode("utf-8"), raw.encode("utf-8"), "sha256").hexdigest()


def create_session(db: Session, lifetime: timedelta, operator_token: str) -> tuple[str, datetime]:
    """A new session; returns the raw cookie value (never stored) and its expiry."""
    now = utc_now()
    db.execute(delete(OperatorSession).where(OperatorSession.expires_at <= now))
    raw = secrets.token_urlsafe(32)
    expires_at = now + lifetime
    db.add(
        OperatorSession(
            token_hash=session_digest(raw, operator_token), created_at=now, expires_at=expires_at
        )
    )
    return raw, expires_at


def find_session(db: Session, raw: str, operator_token: str) -> OperatorSession | None:
    """The live session for a cookie value, or None if unknown, expired, or signed in with a
    different operator token."""
    found = db.scalars(
        select(OperatorSession).where(
            OperatorSession.token_hash == session_digest(raw, operator_token)
        )
    ).one_or_none()
    if found is None or found.expires_at <= utc_now():
        return None
    return found


def end_session(db: Session, raw: str, operator_token: str) -> None:
    db.execute(
        delete(OperatorSession).where(
            OperatorSession.token_hash == session_digest(raw, operator_token)
        )
    )


def end_all_sessions(db: Session) -> int:
    """End every browser session, the caller's included. Returns how many were still live."""
    live = db.scalar(
        select(func.count())
        .select_from(OperatorSession)
        .where(OperatorSession.expires_at > utc_now())
    )
    db.execute(delete(OperatorSession))
    return live or 0


class FailedSignInLimiter:
    """At most ``max_failures`` failed sign-ins per client within ``window_seconds``.

    In memory and per process, which matches the single-process deployment. It slows down
    guessing; the token's length (at least 32 characters) is what makes guessing infeasible.
    """

    def __init__(self, max_failures: int = 5, window_seconds: float = 60.0) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def retry_after(self, client: str) -> int:
        """Seconds until ``client`` may try again; 0 if it may try now."""
        with self._lock:
            attempts = self._recent(client, time.monotonic())
            if len(attempts) < self.max_failures:
                return 0
            return max(1, int(attempts[0] + self.window_seconds - time.monotonic()) + 1)

    def record_failure(self, client: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._recent(client, now).append(now)

    def _recent(self, client: str, now: float) -> deque[float]:
        attempts = self._failures.setdefault(client, deque())
        while attempts and attempts[0] <= now - self.window_seconds:
            attempts.popleft()
        return attempts
