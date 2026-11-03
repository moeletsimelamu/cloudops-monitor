"""Interpreting GitHub Actions run statuses (docs/ARCHITECTURE.md §6.1)."""

from dataclasses import dataclass
from typing import Literal

Outcome = Literal["queued", "in_progress", "succeeded", "failed", "cancelled", "needs_attention"]

_FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
_CANCELLED_CONCLUSIONS = frozenset({"cancelled", "skipped"})
_TERMINAL_OUTCOMES = frozenset({"succeeded", "failed", "cancelled", "needs_attention"})


def deployment_outcome(status: str, conclusion: str | None) -> Outcome:
    if status == "in_progress":
        return "in_progress"
    if status != "completed":  # queued, waiting, requested, pending
        return "queued"
    if conclusion == "success":
        return "succeeded"
    if conclusion in _FAILED_CONCLUSIONS:
        return "failed"
    if conclusion in _CANCELLED_CONCLUSIONS:
        return "cancelled"
    # action_required, neutral, stale, or anything GitHub adds later: never assume it went well.
    return "needs_attention"


def is_terminal(outcome: str) -> bool:
    return outcome in _TERMINAL_OUTCOMES


@dataclass(frozen=True)
class OutcomeEvent:
    type: str
    severity: Literal["info", "warning", "error"]
    verb: str


OUTCOME_EVENTS: dict[str, OutcomeEvent] = {
    "queued": OutcomeEvent("deployment.started", "info", "started"),
    "in_progress": OutcomeEvent("deployment.started", "info", "started"),
    "succeeded": OutcomeEvent("deployment.succeeded", "info", "succeeded"),
    "failed": OutcomeEvent("deployment.failed", "error", "failed"),
    "cancelled": OutcomeEvent("deployment.cancelled", "warning", "was cancelled"),
    "needs_attention": OutcomeEvent("deployment.needs_attention", "warning", "needs attention"),
}
