"""Deployment history, read from GitHub Actions, and the state of that integration.

There is deliberately no route that starts a deployment: see docs/SECURITY.md §6.
"""

import time
from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from cloudops.api.deps import OperatorDep, SessionDep, SettingsDep
from cloudops.api.params import Cursor, Limit, before_cursor, encode_cursor, optional_time_range
from cloudops.db.models import Application, Deployment, SystemState
from cloudops.db.session import run_db_work, session_scope
from cloudops.deployments.sync import (
    DeploymentSync,
    SyncAlreadyRunningError,
    SyncResult,
    sync_state_key,
)
from cloudops.errors import AppError, NotFoundError

router = APIRouter(tags=["deployments"], dependencies=[OperatorDep])

MANUAL_SYNC_INTERVAL_SECONDS = 10.0
OutcomeName = Literal[
    "queued", "in_progress", "succeeded", "failed", "cancelled", "needs_attention"
]


class DeploymentOut(BaseModel):
    id: int
    application_slug: str
    application_name: str
    environment: str
    source: str
    external_id: str
    attempt: int
    run_number: int | None
    workflow_name: str | None
    commit_sha: str
    branch: str | None
    trigger: str | None
    actor: str | None
    title: str | None
    outcome: OutcomeName
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: int | None
    url: str | None
    synced_at: datetime


class DeploymentPage(BaseModel):
    items: list[DeploymentOut]
    next_cursor: str | None


def deployment_out(deployment: Deployment, app: Application) -> DeploymentOut:
    duration = None
    if deployment.started_at and deployment.completed_at:
        duration = max(0, int((deployment.completed_at - deployment.started_at).total_seconds()))
    return DeploymentOut(
        id=deployment.id,
        application_slug=app.slug,
        application_name=app.name,
        environment=app.environment,
        source=deployment.source,
        external_id=deployment.external_id,
        attempt=deployment.attempt,
        run_number=deployment.run_number,
        workflow_name=deployment.workflow_name,
        commit_sha=deployment.commit_sha,
        branch=deployment.branch,
        trigger=deployment.trigger,
        actor=deployment.actor,
        title=deployment.title,
        outcome=deployment.outcome,
        queued_at=deployment.queued_at,
        started_at=deployment.started_at,
        completed_at=deployment.completed_at,
        duration_seconds=duration,
        url=deployment.url,
        synced_at=deployment.synced_at,
    )


@router.get("/deployments", response_model=DeploymentPage)
def list_deployments(
    db: SessionDep,
    window: Annotated[tuple[datetime | None, datetime | None], Depends(optional_time_range)],
    application: Annotated[str | None, Query(max_length=40)] = None,
    outcome: Annotated[OutcomeName | None, Query()] = None,
    branch: Annotated[str | None, Query(max_length=255)] = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> DeploymentPage:
    """Deployments, newest first, within the time range if one is given."""
    query = select(Deployment, Application).join(
        Application, Deployment.application_id == Application.id
    )
    start, end = window
    if start:
        query = query.where(Deployment.queued_at >= start)
    if end:
        query = query.where(Deployment.queued_at <= end)
    if application:
        app_id = db.scalar(select(Application.id).where(Application.slug == application))
        if app_id is None:
            raise NotFoundError(f"There is no application '{application}'.")
        query = query.where(Deployment.application_id == app_id)
    if outcome:
        query = query.where(Deployment.outcome == outcome)
    if branch:
        query = query.where(Deployment.branch == branch)
    if cursor:
        query = query.where(before_cursor(cursor, Deployment.queued_at, Deployment.id))
    rows = db.execute(
        query.order_by(Deployment.queued_at.desc(), Deployment.id.desc()).limit(limit + 1)
    ).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1][0]
        next_cursor = encode_cursor(last.queued_at, last.id)
    return DeploymentPage(
        items=[deployment_out(deployment, app) for deployment, app in page],
        next_cursor=next_cursor,
    )


class DeploymentSourceOut(BaseModel):
    application_slug: str
    application_name: str
    github_repo: str
    deploy_workflow: str
    deploy_branch: str | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_error_kind: str | None
    last_error_message: str | None


class DeploymentSourcesOut(BaseModel):
    token_configured: bool
    sync_interval_seconds: int
    rate_limit_remaining: int | None
    rate_limit_reset_at: datetime | None
    last_run_at: datetime | None
    sources: list[DeploymentSourceOut]


@router.get("/deployments/sources", response_model=DeploymentSourcesOut)
def deployment_sources(
    request: Request, db: SessionDep, settings: SettingsDep
) -> DeploymentSourcesOut:
    """Which applications read deployments from where, and whether that is working."""
    sync: DeploymentSync = request.app.state.deployment_sync
    apps = db.scalars(
        select(Application)
        .where(Application.github_repo.is_not(None), Application.deploy_workflow.is_not(None))
        .order_by(Application.name)
    ).all()
    keys = [sync_state_key(app.id) for app in apps]
    states = {
        state.key: state.value
        for state in db.scalars(select(SystemState).where(SystemState.key.in_(keys)))
    }
    sources = []
    for app in apps:
        state = states.get(sync_state_key(app.id), {})
        sources.append(
            DeploymentSourceOut(
                application_slug=app.slug,
                application_name=app.name,
                github_repo=app.github_repo or "",
                deploy_workflow=app.deploy_workflow or "",
                deploy_branch=app.deploy_branch,
                last_attempt_at=state.get("last_attempt_at"),
                last_success_at=state.get("last_success_at"),
                last_error_kind=state.get("last_error_kind"),
                last_error_message=state.get("last_error_message"),
            )
        )
    return DeploymentSourcesOut(
        token_configured=settings.github_token is not None,
        sync_interval_seconds=settings.github_sync_seconds,
        rate_limit_remaining=sync.rate_remaining,
        rate_limit_reset_at=sync.rate_reset_at,
        last_run_at=sync.last_run_at,
        sources=sources,
    )


class SyncResultOut(BaseModel):
    status: Literal["synced", "not_modified", "skipped", "failed"]
    created: int
    updated: int
    error_kind: str | None
    message: str | None


def _application_id_with_source(factory: sessionmaker[Session], slug: str) -> int:
    with session_scope(factory) as db:
        app = db.scalars(select(Application).where(Application.slug == slug)).one_or_none()
        if app is None:
            raise NotFoundError(f"There is no application '{slug}'.")
        if not app.github_repo or not app.deploy_workflow:
            raise AppError(
                "This application has no deployment source. Set github_repo and deploy_workflow.",
                code="no_deployment_source",
                status_code=HTTPStatus.CONFLICT,
            )
        return app.id


@router.post("/applications/{slug}/deployments/sync", response_model=SyncResultOut)
async def sync_now(slug: str, request: Request) -> SyncResultOut:
    """Read this application's workflow runs from GitHub now. Reads only: starts nothing."""
    app_id = await run_db_work(_application_id_with_source, request.app.state.session_factory, slug)
    last_manual: dict[int, float] = request.app.state.manual_syncs
    wait = MANUAL_SYNC_INTERVAL_SECONDS - (time.monotonic() - last_manual.get(app_id, -1e9))
    if wait > 0:
        raise AppError(
            f"This was synced moments ago. Try again in {int(wait) + 1} seconds.",
            code="too_many_syncs",
            status_code=HTTPStatus.TOO_MANY_REQUESTS,
            headers={"Retry-After": str(int(wait) + 1)},
        )
    sync: DeploymentSync = request.app.state.deployment_sync
    last_manual[app_id] = time.monotonic()
    try:
        result: SyncResult = await sync.sync_application(app_id)
    except SyncAlreadyRunningError:
        raise AppError(
            "A sync of this application is already running.",
            code="sync_in_progress",
            status_code=HTTPStatus.CONFLICT,
        ) from None
    return SyncResultOut(
        status=result.status,
        created=result.created,
        updated=result.updated,
        error_kind=result.error_kind,
        message=result.message,
    )
