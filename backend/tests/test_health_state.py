from datetime import UTC, datetime, timedelta

import pytest

from cloudops.domain.health_state import (
    HealthState,
    Transition,
    display_status,
    next_state,
    stale_after,
    transition,
)

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("current", "signals", "expected"),
    [
        # From unknown, the first conclusive check decides directly.
        ("unknown", ["ok"], "healthy"),
        ("unknown", ["warn"], "degraded"),
        ("unknown", ["fail"], "unavailable"),
        # One bad check does not flip a healthy application (F = 2).
        ("healthy", ["ok", "warn"], "healthy"),
        ("healthy", ["ok", "fail"], "healthy"),
        ("healthy", ["warn", "warn"], "degraded"),
        ("healthy", ["warn", "fail"], "degraded"),
        ("healthy", ["fail", "fail"], "unavailable"),
        # Escalation and improvement inside an incident.
        ("degraded", ["fail", "fail"], "unavailable"),
        ("unavailable", ["fail", "warn"], "degraded"),
        # One good check does not declare recovery (R = 2).
        ("degraded", ["warn", "ok"], "degraded"),
        ("unavailable", ["fail", "ok"], "unavailable"),
        ("degraded", ["ok", "ok"], "healthy"),
        # Alternation keeps the current state (hysteresis).
        ("degraded", ["ok", "warn"], "degraded"),
        ("healthy", ["warn", "ok"], "healthy"),
        # Too few signals for either threshold: unchanged.
        ("healthy", ["fail"], "healthy"),
        ("healthy", [], "healthy"),
    ],
)
def test_state_machine_with_default_thresholds(
    current: HealthState, signals: list[str], expected: HealthState
) -> None:
    assert next_state(current, signals, failure_threshold=2, recovery_threshold=2) == expected


def test_thresholds_of_one_react_immediately() -> None:
    assert next_state("healthy", ["warn"], 1, 1) == "degraded"
    assert next_state("degraded", ["ok"], 1, 1) == "healthy"


def test_higher_thresholds_need_longer_runs() -> None:
    assert next_state("healthy", ["fail", "fail"], 3, 3) == "healthy"
    assert next_state("healthy", ["fail", "fail", "fail"], 3, 3) == "unavailable"
    assert next_state("unavailable", ["ok", "ok"], 3, 3) == "unavailable"
    assert next_state("unavailable", ["fail", "ok", "ok", "ok"], 3, 3) == "healthy"


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("healthy", "healthy", Transition.NONE),
        ("unknown", "healthy", Transition.FIRST_HEALTHY),
        ("unknown", "degraded", Transition.OPENED),
        ("unknown", "unavailable", Transition.OPENED),
        ("healthy", "degraded", Transition.OPENED),
        ("healthy", "unavailable", Transition.OPENED),
        ("degraded", "unavailable", Transition.ESCALATED),
        ("unavailable", "degraded", Transition.IMPROVED),
        ("degraded", "healthy", Transition.RECOVERED),
        ("unavailable", "healthy", Transition.RECOVERED),
    ],
)
def test_transitions(old: HealthState, new: HealthState, expected: Transition) -> None:
    assert transition(old, new) == expected


def test_stale_after_is_three_intervals_with_a_two_minute_floor() -> None:
    assert stale_after(15) == timedelta(minutes=2)
    assert stale_after(60) == timedelta(minutes=3)
    assert stale_after(600) == timedelta(minutes=30)


def _display(**overrides: object) -> str:
    arguments: dict[str, object] = {
        "state": "healthy",
        "monitoring_enabled": True,
        "last_observed_at": NOW - timedelta(seconds=30),
        "interval_seconds": 60,
        "now": NOW,
    }
    arguments.update(overrides)
    return display_status(**arguments).status  # type: ignore[arg-type]


def test_display_status_shows_the_state_while_fresh() -> None:
    assert _display() == "healthy"
    assert _display(state="degraded") == "degraded"


def test_an_old_success_never_looks_healthy() -> None:
    assert _display(last_observed_at=NOW - timedelta(minutes=3)) == "healthy"  # exactly due
    assert _display(last_observed_at=NOW - timedelta(minutes=3, seconds=1)) == "stale"
    old = display_status(
        state="healthy",
        monitoring_enabled=True,
        last_observed_at=NOW - timedelta(hours=1),
        interval_seconds=60,
        now=NOW,
    )
    assert old.stale_since == NOW - timedelta(minutes=57)


def test_paused_and_pending() -> None:
    assert _display(monitoring_enabled=False) == "paused"
    assert _display(last_observed_at=None, state="unknown") == "pending"
