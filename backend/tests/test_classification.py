import pytest

from cloudops.domain.classification import (
    MAX_DETAIL_LENGTH,
    Observation,
    ProbeError,
    classify,
    reported_status,
)

SLOW = 1000


def _http(status: int, duration: int = 50, body: bytes = b"", location: str | None = None) -> str:
    outcome = classify(
        Observation(duration_ms=duration, status_code=status, body=body, location=location), SLOW
    )
    return f"{outcome.signal}/{outcome.reason}"


@pytest.mark.parametrize(
    ("status", "body", "duration", "expected"),
    [
        (200, b"", 50, "ok/None"),
        (204, b"", 50, "ok/None"),
        (200, b'{"status": "pass"}', 50, "ok/None"),
        (200, b'{"status": "UP"}', 50, "ok/None"),
        (200, b"", 1001, "warn/slow_response"),
        (200, b"", 1000, "ok/None"),  # the threshold itself is not slow
        (200, b'{"status": "warn"}', 50, "warn/reported_degraded"),
        (200, b'{"status": "Degraded"}', 50, "warn/reported_degraded"),
        (200, b'{"status": "fail"}', 50, "fail/reported_unhealthy"),
        (200, b'{"status": "down"}', 2000, "fail/reported_unhealthy"),  # reported beats slow
        (200, b'{"status": 1}', 50, "ok/None"),  # non-string status is ignored
        (200, b"<html>OK</html>", 50, "ok/None"),
        (200, b"{not json", 50, "ok/None"),
        (301, b"", 50, "warn/unexpected_redirect"),
        (307, b"", 50, "warn/unexpected_redirect"),
        (404, b"", 50, "warn/client_error"),
        (401, b"", 50, "warn/client_error"),
        (403, b"", 50, "warn/client_error"),
        (429, b"", 50, "warn/rate_limited"),
        (500, b"", 50, "warn/server_error"),
        (501, b"", 50, "warn/server_error"),
        (507, b"", 50, "warn/server_error"),
        (502, b"", 50, "fail/service_unavailable"),
        (503, b'{"status": "pass"}', 50, "fail/service_unavailable"),  # body ignored on 503
        (504, b"", 50, "fail/service_unavailable"),
        (101, b"", 50, "warn/unexpected_status"),
        (600, b"", 50, "warn/unexpected_status"),
    ],
)
def test_http_responses(status: int, body: bytes, duration: int, expected: str) -> None:
    assert _http(status, duration, body) == expected


@pytest.mark.parametrize(
    ("error", "expected_signal"),
    [
        (ProbeError.TIMEOUT, "fail"),
        (ProbeError.DNS_FAILURE, "fail"),
        (ProbeError.CONNECTION_FAILED, "fail"),
        (ProbeError.TLS_ERROR, "fail"),
        (ProbeError.PROTOCOL_ERROR, "fail"),
        (ProbeError.TARGET_BLOCKED, "inconclusive"),
        (ProbeError.MONITOR_ERROR, "inconclusive"),
    ],
)
def test_probe_errors(error: ProbeError, expected_signal: str) -> None:
    outcome = classify(Observation(duration_ms=5000, error=error, error_detail="x"), SLOW)
    assert outcome.signal == expected_signal
    assert outcome.reason == error.value
    assert outcome.detail is not None
    assert outcome.detail.endswith(": x")


def test_missing_status_and_error_is_a_monitor_error() -> None:
    outcome = classify(Observation(duration_ms=None), SLOW)
    assert (outcome.signal, outcome.reason) == ("inconclusive", "monitor_error")


def test_redirect_detail_names_the_target_and_details_are_bounded() -> None:
    outcome = classify(
        Observation(duration_ms=5, status_code=302, location="https://sso.example/login"), SLOW
    )
    assert outcome.detail is not None
    assert "https://sso.example/login" in outcome.detail
    long = classify(
        Observation(duration_ms=5, error=ProbeError.MONITOR_ERROR, error_detail="y" * 1000), SLOW
    )
    assert long.detail is not None
    assert len(long.detail) == MAX_DETAIL_LENGTH


def test_server_error_detail_is_readable() -> None:
    outcome = classify(Observation(duration_ms=84, status_code=500), SLOW)
    assert outcome.detail == "HTTP 500: the application is failing"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b'{"status":"pass","checks":{}}', "pass"),
        (b'  {"status": " Warn "}', "warn"),
        (b"[1, 2]", None),
        (b'{"state": "ok"}', None),
        (b"\xff\xfe{", None),
        (b"", None),
    ],
)
def test_reported_status(body: bytes, expected: str | None) -> None:
    assert reported_status(body) == expected
