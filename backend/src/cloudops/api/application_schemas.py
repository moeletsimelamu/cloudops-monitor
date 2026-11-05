"""Request and response bodies for the application registry and its history."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Slug = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$", max_length=40)
]
Environment = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{1,19}$")]
GitHubRepo = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$", max_length=140)
]
WorkflowFile = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,95}\.ya?ml$", max_length=100)
]


def _git_branch(value: str) -> str:
    """A simplified form of git's branch-name rules."""
    if ".." in value or "//" in value or value.startswith("/") or value.endswith("/"):
        raise ValueError("This is not a valid branch name.")
    return value


BranchName = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9._/-]{1,255}$"),
    AfterValidator(_git_branch),
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]

DisplayStatus = Literal["paused", "pending", "stale", "healthy", "degraded", "unavailable"]


class MonitoringSettings(BaseModel):
    """Fields shared by create and update, validated together."""

    model_config = ConfigDict(extra="forbid")

    name: Name
    description: Description = ""
    environment: Environment
    check_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2048)]
    interval_seconds: int = Field(default=60, ge=15, le=3600)
    timeout_seconds: int = Field(default=5, ge=1, le=30)
    slow_threshold_ms: int = Field(default=1000, ge=50, le=30_000)
    failure_threshold: int = Field(default=2, ge=1, le=5)
    recovery_threshold: int = Field(default=2, ge=1, le=5)
    monitoring_enabled: bool = True
    github_repo: GitHubRepo | None = None
    deploy_workflow: WorkflowFile | None = None
    deploy_branch: BranchName | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.timeout_seconds >= self.interval_seconds:
            raise ValueError("timeout_seconds must be shorter than interval_seconds.")
        if self.slow_threshold_ms >= self.timeout_seconds * 1000:
            raise ValueError("slow_threshold_ms must be shorter than the timeout.")
        if (self.github_repo is None) != (self.deploy_workflow is None):
            raise ValueError(
                "github_repo and deploy_workflow go together: set both to record deployments, "
                "or neither."
            )
        if self.deploy_branch is not None and self.github_repo is None:
            raise ValueError("deploy_branch needs github_repo and deploy_workflow.")
        return self


class ApplicationCreate(MonitoringSettings):
    slug: Slug


class ApplicationUpdate(BaseModel):
    """Partial update: only the fields sent are changed. The slug cannot change."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    description: Description | None = None
    environment: Environment | None = None
    check_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2048)] | None = (
        None
    )
    interval_seconds: int | None = Field(default=None, ge=15, le=3600)
    timeout_seconds: int | None = Field(default=None, ge=1, le=30)
    slow_threshold_ms: int | None = Field(default=None, ge=50, le=30_000)
    failure_threshold: int | None = Field(default=None, ge=1, le=5)
    recovery_threshold: int | None = Field(default=None, ge=1, le=5)
    github_repo: GitHubRepo | None = None
    deploy_workflow: WorkflowFile | None = None
    deploy_branch: BranchName | None = None


class CheckOut(BaseModel):
    id: int
    started_at: datetime
    duration_ms: int | None
    http_status: int | None
    signal: Literal["ok", "warn", "fail", "inconclusive"]
    reason: str | None
    detail: str | None
    resolved_ip: str | None
    state_after: str


class PrecedingDeploymentOut(BaseModel):
    """A deployment close before an incident. Timing only: it does not establish a cause."""

    id: int
    run_number: int | None
    commit_sha: str
    outcome: str
    url: str | None
    relation: Literal["in_progress", "completed_before"]
    minutes_before: int
    summary: str


class IncidentOut(BaseModel):
    id: int
    opened_at: datetime
    closed_at: datetime | None
    worst_state: Literal["degraded", "unavailable"]
    opening_reason: str | None
    duration_seconds: int | None  # None while open
    preceding_deployment: PrecedingDeploymentOut | None = None


class ApplicationOut(BaseModel):
    id: int
    slug: str
    name: str
    description: str
    environment: str
    check_url: str
    interval_seconds: int
    timeout_seconds: int
    slow_threshold_ms: int
    failure_threshold: int
    recovery_threshold: int
    monitoring_enabled: bool
    health_state: Literal["unknown", "healthy", "degraded", "unavailable"]
    status: DisplayStatus
    stale_since: datetime | None
    state_since: datetime | None
    last_observed_at: datetime | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_failure_reason: str | None
    next_check_at: datetime | None
    github_repo: str | None
    deploy_workflow: str | None
    deploy_branch: str | None
    has_ingest_key: bool
    open_incident: IncidentOut | None
    latest_check: CheckOut | None
    created_at: datetime
    updated_at: datetime


class ApplicationList(BaseModel):
    items: list[ApplicationOut]
    generated_at: datetime


class CheckPage(BaseModel):
    items: list[CheckOut]
    next_before: int | None = Field(
        description="Pass as ?before= to get the next (older) page; null when there is none."
    )


class IncidentList(BaseModel):
    items: list[IncidentOut]


class AvailabilityOut(BaseModel):
    checks: int
    ok: int
    warn: int
    fail: int
    expected_checks: float
    coverage: float | None
    availability: float | None
    insufficient_reason: str | None


class BucketOut(BaseModel):
    start: datetime
    checks: int
    failed: int
    worst: Literal["ok", "warn", "fail"] | None
    p50_ms: int | None
    p95_ms: int | None


class MetricsOut(BaseModel):
    window: str
    window_start: datetime
    window_end: datetime
    bucket_seconds: int
    availability: AvailabilityOut
    p50_ms: int | None
    p95_ms: int | None
    buckets: list[BucketOut]
    definitions: dict[str, str]


class IngestKeyOut(BaseModel):
    ingest_key: str = Field(description="Shown once. Only a hash is stored; rotate it if lost.")
    application_slug: str


class RequestBucketOut(BaseModel):
    start: datetime
    requests: int
    errors: int  # responses with a 5xx status


class RequestMetricsOut(BaseModel):
    window: str
    window_start: datetime
    window_end: datetime
    bucket_seconds: int
    has_data: bool
    requests: int
    errors: int
    buckets: list[RequestBucketOut]
    source: str
