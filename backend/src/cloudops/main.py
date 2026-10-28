"""Application factory.

Run locally with ``uvicorn cloudops.main:create_app --factory --port 8300`` (binds to 127.0.0.1
by default).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from cloudops import __version__
from cloudops.api import applications, auth, health, system
from cloudops.auth import FailedSignInLimiter
from cloudops.config import ConfigurationError, Settings, check_settings
from cloudops.db.migrate import upgrade_to_head
from cloudops.db.session import create_db_engine, create_session_factory
from cloudops.errors import register_error_handlers
from cloudops.logging_setup import configure_logging
from cloudops.middleware import HostAndOriginGuardMiddleware, RequestContextMiddleware
from cloudops.monitoring.runner import CheckRunner
from cloudops.monitoring.worker import MonitoringWorker

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    report = check_settings(settings)
    if report.errors:
        raise ConfigurationError(report.errors)
    configure_logging(settings.log_level, settings.log_format, settings.secret_values())

    engine = create_db_engine(settings.database_path)
    session_factory = create_session_factory(engine)
    check_runner = CheckRunner(session_factory, settings)
    worker = (
        MonitoringWorker(session_factory, settings, check_runner)
        if settings.worker_enabled
        else None
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if settings.auto_migrate:
            upgrade_to_head(settings.database_path)
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

    register_error_handlers(app)
    # RequestContextMiddleware is added last so it is outermost: request IDs and security
    # headers also apply to responses produced by the guard.
    app.add_middleware(HostAndOriginGuardMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(RequestContextMiddleware)
    for router in (health.router, auth.router, system.router, applications.router):
        app.include_router(router, prefix="/api")
    return app
