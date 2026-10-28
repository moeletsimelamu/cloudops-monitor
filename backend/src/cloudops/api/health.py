"""Liveness and readiness of CloudOps Monitor itself (docs/ARCHITECTURE.md §7.1).

- Liveness: the process is running and serving requests. It never touches the database, so a
  database problem does not get the container restarted in a loop.
- Readiness: CloudOps can do its job. Each check reports only ``ok``/``error``/``skipped``;
  the reasons go to the server log and, for the operator, to the System view.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from cloudops import __version__
from cloudops.db.migrate import current_revision, head_revision
from cloudops.db.types import utc_now
from cloudops.monitoring.worker import MonitoringWorker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])

CheckStatus = Literal["ok", "error", "skipped"]


class LivenessResponse(BaseModel):
    status: Literal["alive"] = "alive"
    version: str = __version__


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckStatus]


@router.get("/live", response_model=LivenessResponse)
async def live() -> LivenessResponse:
    return LivenessResponse()


def readiness_checks(engine: Engine) -> dict[str, CheckStatus]:
    checks: dict[str, CheckStatus] = {"database": "ok", "schema": "skipped"}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            current = current_revision(connection)
    except SQLAlchemyError:
        logger.exception("Readiness: the database is unavailable")
        checks["database"] = "error"
        return checks
    expected = head_revision()
    if current == expected:
        checks["schema"] = "ok"
    else:
        logger.error(
            "Readiness: database schema is at revision %s, expected %s",
            current,
            expected,
            extra={"fields": {"event": "readiness.schema_mismatch"}},
        )
        checks["schema"] = "error"
    return checks


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
def ready(request: Request) -> JSONResponse:
    checks = readiness_checks(request.app.state.engine)
    worker: MonitoringWorker | None = request.app.state.worker
    if worker is None:
        checks["worker"] = "skipped"  # disabled by configuration (CLOUDOPS_WORKER_ENABLED=false)
    elif worker.is_healthy(utc_now()):
        checks["worker"] = "ok"
    else:
        logger.error(
            "Readiness: the monitoring worker is not running or has stopped ticking",
            extra={"fields": {"event": "readiness.worker_stalled"}},
        )
        checks["worker"] = "error"
    is_ready = all(status != "error" for status in checks.values())
    body = ReadinessResponse(status="ready" if is_ready else "not_ready", checks=checks)
    return JSONResponse(status_code=200 if is_ready else 503, content=body.model_dump())
