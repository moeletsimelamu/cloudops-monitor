"""A fake GitHub Actions API, with payloads shaped like the real ones (trimmed to useful fields).

A test double only: nothing built from this is ever stored outside a test's temporary database.
"""

from collections.abc import Callable
from typing import Any

import httpx2

from cloudops.deployments.github_client import GitHubClient


def run_payload(run_id: int = 812, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": run_id,
        "name": "Deploy",
        "display_title": "Fix order totals",
        "head_branch": "main",
        "head_sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        "run_number": 57,
        "run_attempt": 1,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "workflow_id": 123,
        "html_url": f"https://github.com/acme/orders/actions/runs/{run_id}",
        "created_at": "2026-10-27T14:00:00Z",
        "run_started_at": "2026-10-27T14:00:05Z",
        "updated_at": "2026-10-27T14:03:00Z",
        "actor": {"login": "octocat", "id": 1},
        "repository": {"full_name": "acme/orders"},
    }
    payload.update(overrides)
    return payload


class FakeGitHub:
    """Serves whatever runs are currently set, with ETags, and records the requests."""

    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self.requests: list[httpx2.Request] = []
        self.failure: Callable[[httpx2.Request], httpx2.Response] | None = None
        self.rate_remaining = "4999"
        self.rate_reset = "1800000000"

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.failure is not None:
            return self.failure(request)
        headers = {
            "X-RateLimit-Remaining": self.rate_remaining,
            "X-RateLimit-Reset": self.rate_reset,
        }
        etag = f'W/"{hash(str(self.runs))}"'
        if request.headers.get("if-none-match") == etag:
            return httpx2.Response(304, headers=headers)
        return httpx2.Response(
            200,
            json={"total_count": len(self.runs), "workflow_runs": self.runs},
            headers={**headers, "ETag": etag},
        )

    def client(self, token: str | None = None) -> GitHubClient:
        return GitHubClient("https://api.github.com", token, transport=httpx2.MockTransport(self))
