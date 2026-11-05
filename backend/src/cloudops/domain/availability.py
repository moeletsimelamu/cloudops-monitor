"""Availability, coverage and latency statistics over a time window (docs/ARCHITECTURE.md §3.7).

Health checks are samples, so these numbers describe "health-check availability", not uptime.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

MIN_CHECKS_FOR_AVAILABILITY = 10
MIN_COVERAGE_FOR_AVAILABILITY = 0.5


@dataclass(frozen=True)
class CheckSample:
    started_at: datetime
    signal: str  # conclusive signals only: ok / warn / fail
    duration_ms: int | None
    responded: bool  # an HTTP response was received (so the duration is a response time)


@dataclass(frozen=True)
class Availability:
    checks: int
    ok: int
    warn: int
    fail: int
    expected_checks: float
    coverage: float | None  # None when no monitoring was expected in the window
    availability: float | None  # None when there is too little data to say
    insufficient_reason: str | None


def percentile(values: Sequence[int], fraction: float) -> int | None:
    """Nearest-rank percentile (an actual observed value, never interpolated)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def monitored_seconds(
    window_start: datetime,
    window_end: datetime,
    created_at: datetime,
    pause_changes: Sequence[tuple[datetime, bool]],
    paused_before_window: bool,
) -> float:
    """Seconds within the window during which the application existed and was not paused.

    ``pause_changes`` are (time, paused) records in time order; ``paused_before_window`` is the
    pause state at the start of the window.
    """
    start = max(window_start, created_at)
    if start >= window_end:
        return 0.0
    total = 0.0
    paused = paused_before_window
    cursor = start
    for changed_at, now_paused in pause_changes:
        if changed_at <= start:
            paused = now_paused
            continue
        if changed_at >= window_end:
            break
        if not paused:
            total += (changed_at - cursor).total_seconds()
        cursor = changed_at
        paused = now_paused
    if not paused:
        total += (window_end - cursor).total_seconds()
    return total


def availability(
    samples: Sequence[CheckSample], interval_seconds: int, monitored: float
) -> Availability:
    counts = {"ok": 0, "warn": 0, "fail": 0}
    for sample in samples:
        counts[sample.signal] += 1
    total = len(samples)
    expected = monitored / interval_seconds if interval_seconds > 0 else 0.0
    coverage = min(1.0, total / expected) if expected > 0 else None

    reason: str | None = None
    if total < MIN_CHECKS_FOR_AVAILABILITY:
        reason = f"Fewer than {MIN_CHECKS_FOR_AVAILABILITY} checks in this window"
    elif coverage is not None and coverage < MIN_COVERAGE_FOR_AVAILABILITY:
        reason = "Fewer than half of the expected checks ran in this window"
    value = None if reason else (counts["ok"] + counts["warn"]) / total
    return Availability(
        checks=total,
        ok=counts["ok"],
        warn=counts["warn"],
        fail=counts["fail"],
        expected_checks=round(expected, 1),
        coverage=None if coverage is None else round(coverage, 4),
        availability=None if value is None else round(value, 6),
        insufficient_reason=reason,
    )


@dataclass(frozen=True)
class Bucket:
    start: datetime
    checks: int
    failed: int  # warn or fail
    worst: str | None  # the worst signal in the bucket; None when it has no checks
    p50_ms: int | None
    p95_ms: int | None


def _worst_signal(group: Sequence[CheckSample]) -> str | None:
    signals = {sample.signal for sample in group}
    return next((signal for signal in ("fail", "warn", "ok") if signal in signals), None)


def bucket_size(window: timedelta) -> timedelta:
    if window <= timedelta(hours=1):
        return timedelta(minutes=1)
    if window <= timedelta(hours=24):
        return timedelta(minutes=15)
    return timedelta(hours=2)


def buckets(
    samples: Sequence[CheckSample], window_start: datetime, window_end: datetime, size: timedelta
) -> list[Bucket]:
    """Fixed-size buckets covering the window. Empty buckets stay empty (gaps are not filled)."""
    count = max(1, math.ceil((window_end - window_start) / size))
    grouped: list[list[CheckSample]] = [[] for _ in range(count)]
    for sample in samples:
        index = int((sample.started_at - window_start) / size)
        if 0 <= index < count:
            grouped[index].append(sample)
    result = []
    for index, group in enumerate(grouped):
        durations = [s.duration_ms for s in group if s.responded and s.duration_ms is not None]
        result.append(
            Bucket(
                start=window_start + index * size,
                checks=len(group),
                failed=sum(1 for s in group if s.signal != "ok"),
                worst=_worst_signal(group),
                p50_ms=percentile(durations, 0.5),
                p95_ms=percentile(durations, 0.95),
            )
        )
    return result
