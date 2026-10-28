from datetime import UTC, datetime, timedelta

import pytest

from cloudops.domain.availability import (
    CheckSample,
    availability,
    bucket_size,
    buckets,
    monitored_seconds,
    percentile,
)

START = datetime(2026, 10, 27, 0, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _samples(signals: str, every: timedelta = timedelta(minutes=1)) -> list[CheckSample]:
    """'o' ok, 'w' warn, 'f' fail; one sample per ``every`` from START."""
    names = {"o": "ok", "w": "warn", "f": "fail"}
    return [
        CheckSample(START + i * every, names[c], duration_ms=100 + i, responded=c != "f")
        for i, c in enumerate(signals)
    ]


@pytest.mark.parametrize(
    ("values", "fraction", "expected"),
    [
        ([], 0.5, None),
        ([7], 0.95, 7),
        ([1, 2, 3, 4], 0.5, 2),
        ([10, 20, 30, 40, 50, 60, 70, 80, 90, 100], 0.95, 100),
        ([5, 1, 4, 2, 3], 0.5, 3),
        (list(range(1, 101)), 0.95, 95),
    ],
)
def test_nearest_rank_percentile(values: list[int], fraction: float, expected: int | None) -> None:
    assert percentile(values, fraction) == expected


def test_availability_counts_warn_as_available_and_fail_as_not() -> None:
    result = availability(_samples("o" * 50 + "w" * 5 + "f" * 5), 60, monitored=3600)
    assert (result.checks, result.ok, result.warn, result.fail) == (60, 50, 5, 5)
    assert result.expected_checks == 60
    assert result.coverage == 1.0
    assert result.availability == pytest.approx(55 / 60, abs=1e-6)
    assert result.insufficient_reason is None


def test_too_few_checks_gives_no_percentage() -> None:
    result = availability(_samples("o" * 9), 60, monitored=540)
    assert result.availability is None
    assert result.insufficient_reason == "Fewer than 10 checks in this window"


def test_low_coverage_gives_no_percentage() -> None:
    result = availability(_samples("o" * 20), 60, monitored=3600)  # 20 of 60 expected
    assert result.coverage == pytest.approx(0.3333, abs=1e-4)
    assert result.availability is None
    assert result.insufficient_reason is not None
    assert "half of the expected checks" in result.insufficient_reason


def test_nothing_expected_when_not_monitored() -> None:
    result = availability([], 60, monitored=0)
    assert result.coverage is None
    assert result.availability is None


def test_coverage_is_capped_at_one() -> None:
    # Manual "check now" runs can exceed the scheduled count.
    assert availability(_samples("o" * 80), 60, monitored=3600).coverage == 1.0


def test_monitored_seconds_clips_to_creation() -> None:
    assert monitored_seconds(START, START + HOUR, START + timedelta(minutes=30), [], False) == 1800
    assert monitored_seconds(START, START + HOUR, START + 2 * HOUR, [], False) == 0


def test_monitored_seconds_subtracts_paused_periods() -> None:
    changes = [
        (START + timedelta(minutes=10), True),
        (START + timedelta(minutes=25), False),
        (START + timedelta(minutes=50), True),
    ]
    # Monitored 0-10 and 25-50: 35 minutes.
    assert monitored_seconds(START, START + HOUR, START - HOUR, changes, False) == 35 * 60


def test_monitored_seconds_respects_pause_state_before_the_window() -> None:
    changes = [(START - timedelta(minutes=5), True), (START + timedelta(minutes=20), False)]
    assert monitored_seconds(START, START + HOUR, START - HOUR, changes, True) == 40 * 60
    assert monitored_seconds(START, START + HOUR, START - HOUR, [], True) == 0


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        (timedelta(hours=1), timedelta(minutes=1)),
        (timedelta(hours=6), timedelta(minutes=15)),
        (timedelta(hours=24), timedelta(minutes=15)),
        (timedelta(days=7), timedelta(hours=2)),
    ],
)
def test_bucket_size(window: timedelta, expected: timedelta) -> None:
    assert bucket_size(window) == expected


def test_buckets_keep_gaps_empty_and_ignore_failed_durations() -> None:
    samples = [
        *_samples("oof", every=timedelta(minutes=1)),
        CheckSample(START + timedelta(minutes=10), "ok", 500, True),
    ]
    result = buckets(samples, START, START + timedelta(minutes=15), timedelta(minutes=5))
    assert [b.checks for b in result] == [3, 0, 1]
    assert result[0].failed == 1
    assert result[0].p50_ms == 100  # durations 100 and 101; the failed check had no response
    assert result[0].p95_ms == 101
    assert result[1].p50_ms is None
    assert result[2].start == START + timedelta(minutes=10)
