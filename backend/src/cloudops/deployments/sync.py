"""Keeping deployment records in step with GitHub Actions.

GitHub is the source of truth: a record exists only because GitHub reported that run, and is
upserted by run ID and attempt, so syncing repeatedly changes nothing. Events are written when a
run is first seen or changes outcome. A sync that keeps failing records one event, not one per
attempt.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from cloudops.config import Settings
from cloudops.db.models import Application, Deployment, SystemState
from cloudops.db.session import run_db_work, session_scope
from cloudops.db.types import utc_now
from cloudops.deployments.github_client import GitHubClient, GitHubError, GitHubRun, RunsPage
from cloudops.domain.deployment_status import OUTCOME_EVENTS, deployment_outcome, is_terminal
from cloudops.events import record_event

logger = logging.getLogger(__name__)

SOURCE = "github_actions"


def sync_state_key(application_id: int) -> str:
    return f"github.sync.{application_id}"


@dataclass(frozen=True)
class SyncTarget:
    application_id: int
    repo: str
    workflow: str
    branch: str | None
    etag: str | None


@dataclass(frozen=True)
class SyncResult:
    application_id: int
    status: str  # synced, not_modified, skipped, failed
    created: int = 0
    updated: int = 0
    error_kind: str | None = None
    message: str | None = None


class SyncAlreadyRunningError(Exception):
    pass


def _source_id(repo: str, workflow: str, branch: str | None) -> str:
    return f"{repo}|{workflow}|{branch or ''}"


def _deployment_fields(run: GitHubRun, now: datetime) -> dict[str, Any]:
    outcome = deployment_outcome(run.status, run.conclusion)
    url = run.html_url if run.html_url and run.html_url.startswith("https://") else None
    return {
        "run_number": run.run_number,
        "workflow_name": (run.name or "")[:100] or None,
        "commit_sha": run.head_sha[:40],
        "branch": (run.head_branch or "")[:255] or None,
        "trigger": (run.event or "")[:40] or None,
        "actor": ((run.actor.login if run.actor else None) or "")[:100] or None,
        "title": (run.display_title or "")[:300] or None,
        "outcome": outcome,
        "queued_at": run.created_at,
        "started_at": run.run_started_at,
        # The last change to a completed run; see docs/ARCHITECTURE.md §6.1 for why it is close
        # enough and when it is not.
        "completed_at": run.updated_at if is_terminal(outcome) else None,
        "url": url,
        "synced_at": now,
    }


def _record_outcome_event(db: Session, app: Application, deployment: Deployment) -> None:
    described = OUTCOME_EVENTS[deployment.outcome]
    number = f" #{deployment.run_number}" if deployment.run_number else ""
    occurred_at = deployment.completed_at or deployment.started_at or deployment.queued_at
    record_event(
        db,
        type=described.type,
        severity=described.severity,
        summary=(f"{app.name}: deployment{number} ({deployment.commit_sha[:7]}) {described.verb}"),
        occurred_at=occurred_at,
        application_id=app.id,
        deployment_id=deployment.id,
        details={
            "workflow": deployment.workflow_name,
            "commit_sha": deployment.commit_sha,
            "branch": deployment.branch,
            "actor": deployment.actor,
            "url": deployment.url,
        },
    )


def apply_runs(
    db: Session, app: Application, runs: list[GitHubRun], now: datetime
) -> tuple[int, int]:
    """Upsert runs; returns (created, updated). Older runs first, so events read in order."""
    created = updated = 0
    existing = {
        (d.external_id, d.attempt): d
        for d in db.scalars(
            select(Deployment).where(
                Deployment.application_id == app.id,
                Deployment.source == SOURCE,
                Deployment.external_id.in_([str(run.id) for run in runs]),
            )
        )
    }
    for run in sorted(runs, key=lambda r: (r.created_at, r.id, r.run_attempt)):
        fields = _deployment_fields(run, now)
        found = existing.get((str(run.id), run.run_attempt))
        if found is None:
            deployment = Deployment(
                application_id=app.id,
                source=SOURCE,
                external_id=str(run.id),
                attempt=run.run_attempt,
                **fields,
            )
            db.add(deployment)
            db.flush()
            _record_outcome_event(db, app, deployment)
            created += 1
            continue
        previous = found.outcome
        changed = any(
            getattr(found, name) != value for name, value in fields.items() if name != "synced_at"
        )
        found.synced_at = now
        if changed:
            for name, value in fields.items():
                setattr(found, name, value)
            updated += 1
        # queued -> in_progress is the same "started" event, so it is not recorded twice.
        if found.outcome != previous and is_terminal(found.outcome):
            _record_outcome_event(db, app, found)
    return created, updated


class DeploymentSync:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        client: GitHubClient | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.session_factory = session_factory
        token = settings.github_token.get_secret_value() if settings.github_token else None
        self.client = client or GitHubClient(settings.github_api_url, token)
        self.clock = clock
        self.in_flight: set[int] = set()
        self.rate_remaining: int | None = None
        self.rate_reset_at: datetime | None = None
        self.last_run_at: datetime | None = None

    # --- running ---------------------------------------------------------------------------

    async def sync_all(self) -> list[SyncResult]:
        """Sync every application with a deployment source, one after another."""
        self.last_run_at = self.clock()
        results = []
        for application_id in await run_db_work(self._configured_applications):
            try:
                results.append(await self.sync_application(application_id))
            except SyncAlreadyRunningError:
                continue
        return results

    async def sync_application(self, application_id: int) -> SyncResult:
        if application_id in self.in_flight:
            raise SyncAlreadyRunningError
        self.in_flight.add(application_id)
        try:
            return await self._sync(application_id)
        finally:
            self.in_flight.discard(application_id)

    async def _sync(self, application_id: int) -> SyncResult:
        target = await run_db_work(self._load_target, application_id)
        if target is None:
            return SyncResult(application_id, "skipped", message="No deployment source configured.")
        now = self.clock()
        if self.rate_remaining == 0 and self.rate_reset_at and now < self.rate_reset_at:
            return SyncResult(
                application_id,
                "skipped",
                error_kind="rate_limited",
                message=f"Waiting for GitHub's rate limit to reset at {self.rate_reset_at}.",
            )
        try:
            page = await self.client.workflow_runs(
                target.repo, target.workflow, branch=target.branch, etag=target.etag
            )
        except GitHubError as error:
            if error.kind == "rate_limited":
                self.rate_remaining = 0
                self.rate_reset_at = error.rate_reset_at
            await run_db_work(self._record_failure, target, error, now)
            logger.warning(
                "Deployment sync failed: %s",
                error.message,
                extra={
                    "fields": {
                        "event": "deployments.sync_failed",
                        "application_id": application_id,
                        "kind": error.kind,
                    }
                },
            )
            return SyncResult(
                application_id, "failed", error_kind=error.kind, message=error.message
            )
        self.rate_remaining = page.rate_remaining
        self.rate_reset_at = page.rate_reset_at
        return await run_db_work(self._record_success, target, page, now)

    # --- database work (runs in threads) ---------------------------------------------------

    def _configured_applications(self) -> list[int]:
        with session_scope(self.session_factory) as db:
            return list(
                db.scalars(
                    select(Application.id).where(
                        Application.github_repo.is_not(None),
                        Application.deploy_workflow.is_not(None),
                    )
                )
            )

    def _load_target(self, application_id: int) -> SyncTarget | None:
        with session_scope(self.session_factory) as db:
            app = db.get(Application, application_id)
            if app is None or not app.github_repo or not app.deploy_workflow:
                return None
            state = db.get(SystemState, sync_state_key(application_id))
            value = state.value if state else {}
            source = _source_id(app.github_repo, app.deploy_workflow, app.deploy_branch)
            # An ETag only means something for the query it came from.
            etag = value.get("etag") if value.get("source") == source else None
            return SyncTarget(
                application_id, app.github_repo, app.deploy_workflow, app.deploy_branch, etag
            )

    def _save_state(
        self, db: Session, application_id: int, value: dict[str, Any], now: datetime
    ) -> None:
        key = sync_state_key(application_id)
        state = db.get(SystemState, key)
        if state is None:
            db.add(SystemState(key=key, value=value, updated_at=now))
        else:
            state.value = value
            state.updated_at = now

    def _record_success(self, target: SyncTarget, page: RunsPage, now: datetime) -> SyncResult:
        with session_scope(self.session_factory) as db:
            app = db.get(Application, target.application_id)
            if app is None:
                return SyncResult(target.application_id, "skipped", message="Application removed.")
            state = db.get(SystemState, sync_state_key(app.id))
            previous = state.value if state else {}
            created, updated = (0, 0) if page.not_modified else apply_runs(db, app, page.runs, now)
            if previous.get("last_error_kind"):
                record_event(
                    db,
                    type="deployments.sync_recovered",
                    severity="info",
                    summary=f"{app.name}: reading deployments from GitHub works again",
                    occurred_at=now,
                    application_id=app.id,
                )
            self._save_state(
                db,
                app.id,
                {
                    "source": _source_id(target.repo, target.workflow, target.branch),
                    "etag": page.etag,
                    "last_attempt_at": now.isoformat(),
                    "last_success_at": now.isoformat(),
                    "last_error_kind": None,
                    "last_error_message": None,
                },
                now,
            )
            status = "not_modified" if page.not_modified else "synced"
            return SyncResult(app.id, status, created=created, updated=updated)

    def _record_failure(self, target: SyncTarget, error: GitHubError, now: datetime) -> None:
        with session_scope(self.session_factory) as db:
            app = db.get(Application, target.application_id)
            if app is None:
                return
            state = db.get(SystemState, sync_state_key(app.id))
            previous = state.value if state else {}
            if previous.get("last_error_kind") is None:  # only when it starts failing
                record_event(
                    db,
                    type="deployments.sync_failed",
                    severity="warning",
                    summary=f"{app.name}: could not read deployments. {error.message}",
                    occurred_at=now,
                    application_id=app.id,
                    details={"kind": error.kind},
                )
            self._save_state(
                db,
                app.id,
                {
                    **previous,
                    "last_attempt_at": now.isoformat(),
                    "last_error_kind": error.kind,
                    "last_error_message": error.message,
                },
                now,
            )
