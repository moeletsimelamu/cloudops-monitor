"""The operator's view of CloudOps Monitor's own configuration. Secrets are never returned: they
are reported only as set or not set."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from cloudops import __version__
from cloudops.api.deps import OperatorDep, SessionDep, SettingsDep
from cloudops.db.models import Application, HealthCheck, Incident
from cloudops.db.types import utc_now
from cloudops.monitoring.runner import CheckRunner
from cloudops.monitoring.worker import MonitoringWorker

router = APIRouter(prefix="/system", tags=["system"], dependencies=[OperatorDep])

SecretState = Literal["set", "not set"]


class GitHubConfig(BaseModel):
    api_url: str
    token: SecretState
    sync_seconds: int


class RetentionConfig(BaseModel):
    check_days: int
    event_days: int
    log_days: int
    log_max_rows: int


class EffectiveConfig(BaseModel):
    version: str
    auth_mode: str
    operator_token: SecretState
    session_hours: int
    allowed_hosts: list[str]
    trusted_proxies: list[str]
    target_policy: str
    allowed_targets: list[str]
    worker_enabled: bool
    monitor_max_concurrency: int
    github: GitHubConfig
    retention: RetentionConfig
    data_dir: str
    auto_migrate: bool
    log_level: str
    log_format: str
    api_docs: bool
    warnings: list[str]


class WorkerStatus(BaseModel):
    enabled: bool
    running: bool
    healthy: bool
    started_at: datetime | None
    last_tick_at: datetime | None
    loop_lag_ms: float | None
    consecutive_tick_failures: int
    checks_in_flight: int
    last_retention_at: datetime | None
    last_retention_deleted: dict[str, int] | None


class DatabaseStatus(BaseModel):
    size_bytes: int | None
    applications: int
    health_checks: int
    open_incidents: int


class SystemStatus(BaseModel):
    version: str
    generated_at: datetime
    worker: WorkerStatus
    database: DatabaseStatus
    warnings: list[str]


@router.get("/status", response_model=SystemStatus)
def system_status(request: Request, settings: SettingsDep, db: SessionDep) -> SystemStatus:
    """The monitor's own health: worker heartbeat, database size and configuration warnings."""
    now = utc_now()
    worker: MonitoringWorker | None = request.app.state.worker
    runner: CheckRunner = request.app.state.check_runner
    retention = worker.last_retention if worker else None
    try:
        size: int | None = settings.database_path.stat().st_size
    except OSError:
        size = None
    return SystemStatus(
        version=__version__,
        generated_at=now,
        worker=WorkerStatus(
            enabled=worker is not None,
            running=bool(worker and worker.running),
            healthy=bool(worker and worker.is_healthy(now)),
            started_at=worker.started_at if worker else None,
            last_tick_at=worker.last_tick_at if worker else None,
            loop_lag_ms=worker.loop_lag_ms if worker else None,
            consecutive_tick_failures=worker.consecutive_tick_failures if worker else 0,
            checks_in_flight=len(runner.in_flight),
            last_retention_at=retention.ran_at if retention else None,
            last_retention_deleted=(
                {
                    "health_checks": retention.health_checks,
                    "events": retention.events,
                    "log_entries": retention.log_entries,
                    "sessions": retention.sessions,
                }
                if retention
                else None
            ),
        ),
        database=DatabaseStatus(
            size_bytes=size,
            applications=db.scalar(select(func.count(Application.id))) or 0,
            health_checks=db.scalar(select(func.count(HealthCheck.id))) or 0,
            open_incidents=db.scalar(
                select(func.count(Incident.id)).where(Incident.closed_at.is_(None))
            )
            or 0,
        ),
        warnings=request.app.state.config_warnings,
    )


def _state(value: object) -> SecretState:
    return "set" if value is not None else "not set"


@router.get("/config", response_model=EffectiveConfig)
def effective_config(request: Request, settings: SettingsDep) -> EffectiveConfig:
    return EffectiveConfig(
        version=__version__,
        auth_mode=settings.auth_mode,
        operator_token=_state(settings.operator_token),
        session_hours=settings.session_hours,
        allowed_hosts=settings.allowed_hosts,
        trusted_proxies=settings.trusted_proxies,
        target_policy=settings.target_policy,
        allowed_targets=settings.allowed_targets,
        worker_enabled=settings.worker_enabled,
        monitor_max_concurrency=settings.monitor_max_concurrency,
        github=GitHubConfig(
            api_url=settings.github_api_url,
            token=_state(settings.github_token),
            sync_seconds=settings.github_sync_seconds,
        ),
        retention=RetentionConfig(
            check_days=settings.check_retention_days,
            event_days=settings.event_retention_days,
            log_days=settings.log_retention_days,
            log_max_rows=settings.log_max_rows,
        ),
        data_dir=str(settings.data_dir.resolve()),
        auto_migrate=settings.auto_migrate,
        log_level=settings.log_level,
        log_format=settings.log_format,
        api_docs=settings.api_docs,
        warnings=request.app.state.config_warnings,
    )
