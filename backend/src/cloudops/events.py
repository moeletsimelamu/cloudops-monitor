"""Recording operational events (docs/ARCHITECTURE.md §4).

Events are append-only. Callers write them in the same transaction as the change they describe,
so an event exists exactly when its change was committed.
"""

from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy.orm import Session

from cloudops.db.models import OperationalEvent

Severity = Literal["info", "warning", "error", "critical"]


def record_event(
    db: Session,
    *,
    type: str,
    severity: Severity,
    summary: str,
    occurred_at: datetime,
    application_id: int | None = None,
    incident_id: int | None = None,
    deployment_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> OperationalEvent:
    event = OperationalEvent(
        occurred_at=occurred_at,
        type=type,
        severity=severity,
        summary=summary[:300],
        application_id=application_id,
        incident_id=incident_id,
        deployment_id=deployment_id,
        details=details or {},
    )
    db.add(event)
    return event


def humanize_duration(duration: timedelta) -> str:
    """'45 s', '12 min', '3 h 5 min', '2 d 4 h'."""
    seconds = max(0, int(duration.total_seconds()))
    if seconds < 60:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"
