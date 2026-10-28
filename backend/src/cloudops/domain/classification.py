"""Turning one probe observation into a signal (docs/ARCHITECTURE.md §3.3).

A signal is the verdict on a single check: ``ok``, ``warn``, ``fail``, or ``inconclusive`` when
the monitor could not observe the application at all (the fault is on the monitor's side).
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

Signal = Literal["ok", "warn", "fail", "inconclusive"]
CONCLUSIVE_SIGNALS: frozenset[str] = frozenset({"ok", "warn", "fail"})


class ProbeError(StrEnum):
    """Why a probe got no HTTP response."""

    TIMEOUT = "timeout"
    DNS_FAILURE = "dns_failure"
    CONNECTION_FAILED = "connection_failed"
    TLS_ERROR = "tls_error"
    PROTOCOL_ERROR = "protocol_error"
    TARGET_BLOCKED = "target_blocked"
    MONITOR_ERROR = "monitor_error"


@dataclass(frozen=True)
class Observation:
    """What a probe saw. Exactly one of ``status_code`` and ``error`` is set."""

    duration_ms: int | None
    status_code: int | None = None
    body: bytes = b""
    location: str | None = None
    error: ProbeError | None = None
    error_detail: str | None = None
    resolved_ip: str | None = None


@dataclass(frozen=True)
class CheckOutcome:
    signal: Signal
    reason: str | None
    detail: str | None


_OK_WORDS = frozenset({"pass", "ok", "healthy", "up"})
_WARN_WORDS = frozenset({"warn", "degraded"})
_FAIL_WORDS = frozenset({"fail", "down", "unhealthy", "error"})

_ERROR_SIGNALS: dict[ProbeError, Signal] = {
    ProbeError.TIMEOUT: "fail",
    ProbeError.DNS_FAILURE: "fail",
    ProbeError.CONNECTION_FAILED: "fail",
    ProbeError.TLS_ERROR: "fail",
    # The server accepted the connection but did not speak valid HTTP (or hung up mid-response).
    ProbeError.PROTOCOL_ERROR: "fail",
    ProbeError.TARGET_BLOCKED: "inconclusive",
    ProbeError.MONITOR_ERROR: "inconclusive",
}

_ERROR_DETAILS: dict[ProbeError, str] = {
    ProbeError.TIMEOUT: "No response within the timeout",
    ProbeError.DNS_FAILURE: "The host name could not be resolved",
    ProbeError.CONNECTION_FAILED: "The connection was refused or reset",
    ProbeError.TLS_ERROR: "The TLS handshake or certificate check failed",
    ProbeError.PROTOCOL_ERROR: "The server did not return a valid HTTP response",
    ProbeError.TARGET_BLOCKED: "The destination is not allowed by the target policy",
    ProbeError.MONITOR_ERROR: "The monitor failed to run the check",
}

MAX_DETAIL_LENGTH = 300


def reported_status(body: bytes) -> str | None:
    """The lower-cased ``status`` field of a JSON object body, if there is one."""
    if not body.lstrip().startswith(b"{"):
        return None
    try:
        document = json.loads(body)
    except ValueError:  # includes UnicodeDecodeError
        return None
    if not isinstance(document, dict):
        return None
    status = document.get("status")
    return status.strip().lower() if isinstance(status, str) else None


def _detail(text: str) -> str:
    return text if len(text) <= MAX_DETAIL_LENGTH else text[: MAX_DETAIL_LENGTH - 1] + "…"


def classify(observation: Observation, slow_threshold_ms: int) -> CheckOutcome:
    if observation.error is not None or observation.status_code is None:
        error = observation.error or ProbeError.MONITOR_ERROR
        detail = _ERROR_DETAILS[error]
        if observation.error_detail:
            detail = f"{detail}: {observation.error_detail}"
        return CheckOutcome(_ERROR_SIGNALS[error], error.value, _detail(detail))

    status = observation.status_code
    if 200 <= status < 300:
        reported = reported_status(observation.body)
        if reported in _FAIL_WORDS:
            return CheckOutcome(
                "fail", "reported_unhealthy", f"HTTP {status}; the application reports '{reported}'"
            )
        if reported in _WARN_WORDS:
            return CheckOutcome(
                "warn", "reported_degraded", f"HTTP {status}; the application reports '{reported}'"
            )
        duration = observation.duration_ms or 0
        if duration > slow_threshold_ms:
            return CheckOutcome(
                "warn",
                "slow_response",
                f"HTTP {status} in {duration} ms (slower than {slow_threshold_ms} ms)",
            )
        return CheckOutcome("ok", None, None)
    if 300 <= status < 400:
        target = f" to {observation.location}" if observation.location else ""
        return CheckOutcome(
            "warn",
            "unexpected_redirect",
            _detail(f"HTTP {status} redirect{target} (redirects are not followed)"),
        )
    if status == 429:
        return CheckOutcome("warn", "rate_limited", "HTTP 429: the application throttled the check")
    if 400 <= status < 500:
        return CheckOutcome(
            "warn",
            "client_error",
            f"HTTP {status}: the health endpoint is missing or protected; check the URL",
        )
    if status in (502, 503, 504):
        return CheckOutcome("fail", "service_unavailable", f"HTTP {status}: not serving")
    if 500 <= status < 600:
        return CheckOutcome("warn", "server_error", f"HTTP {status}: the application is failing")
    return CheckOutcome("warn", "unexpected_status", f"Unexpected HTTP status {status}")
