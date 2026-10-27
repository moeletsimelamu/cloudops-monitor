"""The operator's view of CloudOps Monitor's own configuration. Secrets are never returned: they
are reported only as set or not set."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from cloudops import __version__
from cloudops.api.deps import OperatorDep, SettingsDep

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
