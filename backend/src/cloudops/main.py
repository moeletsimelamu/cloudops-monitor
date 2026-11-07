"""Application factory.

Run locally with ``uvicorn cloudops.main:create_app --factory --port 8300`` (binds to 127.0.0.1
by default).
"""

import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing

from fastapi import FastAPI
from sqlalchemy.exc import DBAPIError

from cloudops import __version__
from cloudops.api import (
    applications,
    auth,
    deployments,
    events,
    health,
    ingest,
    logs,
    overview,
    system,
)
from cloudops.auth import FailedSignInLimiter
from cloudops.config import ConfigurationError, Settings, check_settings, load_settings
from cloudops.db.migrate import upgrade_to_head
from cloudops.db.session import create_db_engine, create_session_factory
from cloudops.deployments.sync import DeploymentSync
from cloudops.errors import register_error_handlers
from cloudops.ingest.logs import MAX_BODY_BYTES, IngestRateLimiter
from cloudops.logging_setup import configure_logging
from cloudops.middleware import (
    BodySizeLimitMiddleware,
    HostAndOriginGuardMiddleware,
    RequestContextMiddleware,
)
from cloudops.monitoring.runner import CheckRunner
from cloudops.monitoring.worker import MonitoringWorker
from cloudops.spa import register_spa

logger = logging.getLogger(__name__)


class StorageError(RuntimeError):
    """The data directory or the database cannot be used. Already logged when raised."""


def prepare_storage(settings: Settings) -> None:
    """Create the data directory and bring the schema up to date, or, with migrations off, at
    least read the database. A damaged file or a directory the server cannot write is reported
    as one critical record naming the file, instead of surfacing as a traceback."""
    path = settings.database_path
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if settings.auto_migrate:
            upgrade_to_head(path)
        else:
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except (OSError, sqlite3.Error, DBAPIError) as error:
        reason = str(error.orig) if isinstance(error, DBAPIError) else str(error)
        logger.critical(
            "CloudOps Monitor cannot use the database at %s: %s",
            path,
            reason,
            extra={"fields": {"event": "database.unusable", "path": str(path), "reason": reason}},
        )
        raise StorageError(reason) from error


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else load_settings()
    report = check_settings(settings)
    if report.errors:
        raise ConfigurationError(report.errors)
    configure_logging(settings.log_level, settings.log_format, settings.secret_values())

    engine = create_db_engine(settings.database_path)
    session_factory = create_session_factory(engine)
    check_runner = CheckRunner(session_factory, settings)
    deployment_sync = DeploymentSync(session_factory, settings)
    worker = (
        MonitoringWorker(session_factory, settings, check_runner, deployment_sync=deployment_sync)
        if settings.worker_enabled
        else None
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        prepare_storage(settings)
        for warning in report.warnings:
            logger.warning(warning, extra={"fields": {"event": "config.warning"}})
        logger.info(
            "CloudOps Monitor %s started",
            __version__,
            extra={
                "fields": {
                    "event": "app.started",
                    "data_dir": str(settings.data_dir.resolve()),
                    "auth_mode": settings.auth_mode,
                    "target_policy": settings.target_policy,
                }
            },
        )
        if worker is not None:
            await worker.start()
        try:
            yield
        finally:
            if worker is not None:
                await worker.stop()
            engine.dispose()
            logger.info("CloudOps Monitor stopped", extra={"fields": {"event": "app.stopped"}})

    app = FastAPI(
        title="CloudOps Monitor API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.api_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.api_docs else None,
    )
    app.state.settings = settings
    app.state.config_warnings = report.warnings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.sign_in_limiter = FailedSignInLimiter()
    app.state.check_runner = check_runner
    app.state.worker = worker
    app.state.manual_checks = {}  # application id -> time of the last "check now"
    app.state.ingest_limiter = IngestRateLimiter(settings.ingest_max_entries_per_minute)
    app.state.deployment_sync = deployment_sync
    app.state.manual_syncs = {}  # application id -> time of the last "sync now"

    register_error_handlers(app)
    # RequestContextMiddleware is added last so it is outermost: request IDs and security
    # headers also apply to responses produced by the guard.
    app.add_middleware(
        BodySizeLimitMiddleware,
        path="/api/ingest/logs",
        max_bytes=MAX_BODY_BYTES,
        message=f"A log batch can be at most {MAX_BODY_BYTES // 1024} KiB. Send smaller batches.",
    )
    app.add_middleware(HostAndOriginGuardMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(RequestContextMiddleware)
    for router in (
        health.router,
        auth.router,
        system.router,
        applications.router,
        deployments.router,
        events.router,
        logs.router,
        overview.router,
        ingest.router,
    ):
        app.include_router(router, prefix="/api")
    if settings.static_dir is not None:
        register_spa(app, settings.static_dir)  # last: it matches every remaining GET path
    return app
