"""Where monitored applications send their logs. Authenticated by the application's ingest key,
not by the operator, so a key can only ever write into its own application's logs."""

from http import HTTPStatus
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from cloudops.api.deps import SessionDep, SettingsDep, bearer_token
from cloudops.auth import hash_secret
from cloudops.db.models import Application
from cloudops.db.types import utc_now
from cloudops.errors import AppError
from cloudops.ingest.logs import (
    INGEST_KEY_PREFIX,
    MAX_ENTRIES_PER_BATCH,
    IngestRateLimiter,
    store_entries,
    validate_entries,
)

router = APIRouter(prefix="/ingest", tags=["ingest"])


class LogBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: Annotated[list[Any], Field(min_length=1, max_length=MAX_ENTRIES_PER_BATCH)]


class RejectedEntryOut(BaseModel):
    index: int
    errors: list[dict[str, Any]]


class IngestResult(BaseModel):
    accepted: int
    rejected: list[RejectedEntryOut]


def _application_for_key(request: Request, db: SessionDep) -> Application:
    key = bearer_token(request)
    app = None
    if key is not None and key.startswith(INGEST_KEY_PREFIX):
        app = db.scalars(
            select(Application).where(Application.ingest_key_hash == hash_secret(key))
        ).one_or_none()
    if app is None:
        raise AppError(
            "A valid ingest key is required (Authorization: Bearer cok_...). Create one for the "
            "application under POST /api/applications/{slug}/ingest-key.",
            code="invalid_ingest_key",
            status_code=HTTPStatus.UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return app


@router.post("/logs", response_model=IngestResult)
def ingest_logs(
    batch: LogBatch,
    request: Request,
    db: SessionDep,
    settings: SettingsDep,
    app: Annotated[Application, Depends(_application_for_key)],
) -> IngestResult:
    """Store a batch of log entries. Invalid entries are reported by index; the rest are kept."""
    limiter: IngestRateLimiter = request.app.state.ingest_limiter
    wait = limiter.retry_after(app.id, len(batch.entries))
    if wait:
        raise AppError(
            f"More than {limiter.max_per_minute} log entries a minute. Retry in {wait} seconds.",
            code="too_many_log_entries",
            status_code=HTTPStatus.TOO_MANY_REQUESTS,
            headers={"Retry-After": str(wait)},
        )
    now = utc_now()
    valid, rejected = validate_entries(batch.entries, now)
    accepted = store_entries(db, app.id, app.environment, valid, settings.secret_values(), now)
    return IngestResult(
        accepted=accepted,
        rejected=[RejectedEntryOut(index=r.index, errors=r.errors) for r in rejected],
    )
