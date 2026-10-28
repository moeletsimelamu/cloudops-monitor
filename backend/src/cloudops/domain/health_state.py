"""The health state machine, incident transitions and freshness (docs/ARCHITECTURE.md §3.4-§3.6).

Pure functions: the recorder feeds them stored data and persists what they decide.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

HealthState = Literal["unknown", "healthy", "degraded", "unavailable"]
DisplayStatus = Literal["paused", "pending", "stale", "healthy", "degraded", "unavailable"]

_FIRST_STATE: dict[str, HealthState] = {"ok": "healthy", "warn": "degraded", "fail": "unavailable"}
_SEVERITY = {"healthy": 0, "degraded": 1, "unavailable": 2}

MIN_STALE_AFTER = timedelta(minutes=2)


def next_state(
    current: HealthState,
    recent_signals: Sequence[str],
    failure_threshold: int,
    recovery_threshold: int,
) -> HealthState:
    """The state after the newest check.

    ``recent_signals`` are the most recent *conclusive* signals, oldest first, ending with the
    check just made. A single blip does not flip the state: the thresholds require that many
    consecutive agreeing checks.
    """
    if not recent_signals:
        return current
    if current == "unknown":
        return _FIRST_STATE[recent_signals[-1]]
    last_failures = recent_signals[-failure_threshold:]
    if len(last_failures) == failure_threshold:
        if all(signal == "fail" for signal in last_failures):
            return "unavailable"
        if all(signal != "ok" for signal in last_failures):
            return "degraded"
    last_recoveries = recent_signals[-recovery_threshold:]
    if len(last_recoveries) == recovery_threshold and all(s == "ok" for s in last_recoveries):
        return "healthy"
    return current


class Transition(StrEnum):
    NONE = "none"
    OPENED = "opened"  # healthy/unknown -> degraded/unavailable
    ESCALATED = "escalated"  # degraded -> unavailable
    IMPROVED = "improved"  # unavailable -> degraded
    RECOVERED = "recovered"  # degraded/unavailable -> healthy
    FIRST_HEALTHY = "first_healthy"  # unknown -> healthy: no incident, nothing to report


def transition(old: HealthState, new: HealthState) -> Transition:
    if old == new:
        return Transition.NONE
    if new == "healthy":
        return Transition.FIRST_HEALTHY if old == "unknown" else Transition.RECOVERED
    if old in ("healthy", "unknown"):
        return Transition.OPENED
    return Transition.ESCALATED if _SEVERITY[new] > _SEVERITY[old] else Transition.IMPROVED


def stale_after(interval_seconds: int) -> timedelta:
    return max(timedelta(seconds=3 * interval_seconds), MIN_STALE_AFTER)


@dataclass(frozen=True)
class Freshness:
    status: DisplayStatus
    stale_since: datetime | None = None


def display_status(
    *,
    state: HealthState,
    monitoring_enabled: bool,
    last_observed_at: datetime | None,
    interval_seconds: int,
    now: datetime,
) -> Freshness:
    """What the user sees: the stored state, unless it is paused, never observed, or too old."""
    if not monitoring_enabled:
        return Freshness("paused")
    if last_observed_at is None or state == "unknown":
        return Freshness("pending")
    deadline = last_observed_at + stale_after(interval_seconds)
    if now > deadline:
        return Freshness("stale", stale_since=deadline)
    return Freshness(state)
