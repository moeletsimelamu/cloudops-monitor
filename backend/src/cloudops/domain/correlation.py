"""Relating an incident to a nearby deployment (docs/ARCHITECTURE.md §6.3).

This reports timing only. A deployment shortly before an incident is a good first place to look;
it does not show that the deployment caused the incident, and the wording says so.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

CORRELATION_WINDOW = timedelta(minutes=60)

Relation = Literal["in_progress", "completed_before"]


class DeploymentTimes(Protocol):
    @property
    def started_at(self) -> datetime | None: ...
    @property
    def completed_at(self) -> datetime | None: ...


@dataclass(frozen=True)
class Nearby[T]:
    deployment: T
    relation: Relation
    minutes_before: int


def find_preceding_deployment[T: DeploymentTimes](
    opened_at: datetime, deployments: Iterable[T]
) -> Nearby[T] | None:
    """The deployment running when the incident opened, else the latest to finish in the hour
    before it. Deployments that never started, or started after the incident, are ignored."""
    running: list[T] = []
    finished: list[T] = []
    for deployment in deployments:
        started = deployment.started_at
        completed = deployment.completed_at
        if started is None or started > opened_at:
            continue
        if completed is None or completed > opened_at:
            # A run still open long afterwards is more likely a missed update than a live deploy.
            if opened_at - started <= CORRELATION_WINDOW:
                running.append(deployment)
        elif opened_at - completed <= CORRELATION_WINDOW:
            finished.append(deployment)
    if running:
        latest = max(running, key=lambda d: d.started_at or opened_at)
        return Nearby(latest, "in_progress", _minutes(opened_at, latest.started_at or opened_at))
    if finished:
        latest = max(finished, key=lambda d: d.completed_at or opened_at)
        reference = latest.completed_at or opened_at
        return Nearby(latest, "completed_before", _minutes(opened_at, reference))
    return None


def _minutes(later: datetime, earlier: datetime) -> int:
    return max(0, int((later - earlier).total_seconds() // 60))
