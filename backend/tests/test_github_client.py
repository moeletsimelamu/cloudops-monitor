"""The GitHub client against a fake transport: no network, and every error path."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest

from cloudops.deployments.github_client import GitHubClient, GitHubError
from cloudops.domain.deployment_status import deployment_outcome, is_terminal
from tests.github_fake import run_payload

pytestmark = pytest.mark.anyio

TOKEN = "github_pat_" + "S" * 40


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _client(
    handler: Callable[[httpx2.Request], httpx2.Response], token: str | None = TOKEN
) -> GitHubClient:
    return GitHubClient("https://api.github.com", token, transport=httpx2.MockTransport(handler))


@pytest.mark.parametrize(
    ("status", "conclusion", "expected"),
    [
        ("queued", None, "queued"),
        ("waiting", None, "queued"),
        ("requested", None, "queued"),
        ("pending", None, "queued"),
        ("in_progress", None, "in_progress"),
        ("completed", "success", "succeeded"),
        ("completed", "failure", "failed"),
        ("completed", "timed_out", "failed"),
        ("completed", "startup_failure", "failed"),
        ("completed", "cancelled", "cancelled"),
        ("completed", "skipped", "cancelled"),
        ("completed", "action_required", "needs_attention"),
        ("completed", "neutral", "needs_attention"),
        ("completed", "stale", "needs_attention"),
        ("completed", "something_new", "needs_attention"),
        ("completed", None, "needs_attention"),
    ],
)
def test_status_mapping(status: str, conclusion: str | None, expected: str) -> None:
    assert deployment_outcome(status, conclusion) == expected


def test_terminal_outcomes() -> None:
    assert not is_terminal("queued")
    assert not is_terminal("in_progress")
    for outcome in ("succeeded", "failed", "cancelled", "needs_attention"):
        assert is_terminal(outcome)


async def test_request_and_parsed_runs() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json={"total_count": 1, "workflow_runs": [run_payload(812)]},
            headers={
                "ETag": 'W/"abc"',
                "X-RateLimit-Remaining": "4999",
                "X-RateLimit-Reset": "1800000000",
            },
        )

    page = await _client(handler).workflow_runs(
        "moeletsimelamu/cloudops-monitor", "deploy.yml", branch="main"
    )
    request = seen[0]
    assert request.url.path == (
        "/repos/moeletsimelamu/cloudops-monitor/actions/workflows/deploy.yml/runs"
    )
    assert dict(request.url.params) == {"per_page": "20", "branch": "main"}
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["accept"] == "application/vnd.github+json"
    assert "if-none-match" not in request.headers
    assert page.not_modified is False
    assert page.etag == 'W/"abc"'
    assert page.rate_remaining == 4999
    assert page.rate_reset_at == datetime.fromtimestamp(1800000000, UTC)
    run = page.runs[0]
    assert (run.id, run.run_number, run.run_attempt) == (812, 57, 1)
    assert run.actor is not None
    assert run.actor.login == "octocat"


async def test_public_repositories_need_no_token() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert "authorization" not in request.headers
        return httpx2.Response(200, json={"workflow_runs": []})

    page = await _client(handler, token=None).workflow_runs("a/b", "ci.yml")
    assert page.runs == []
    assert page.rate_remaining is None


async def test_etag_makes_unchanged_results_cheap() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["if-none-match"] == 'W/"abc"'
        return httpx2.Response(304, headers={"X-RateLimit-Remaining": "59"})

    page = await _client(handler).workflow_runs("a/b", "ci.yml", etag='W/"abc"')
    assert page.not_modified is True
    assert page.etag == 'W/"abc"'
    assert page.rate_remaining == 59


@pytest.mark.parametrize(
    ("status", "headers", "kind", "fragment"),
    [
        (401, {}, "unauthorized", "rejected CLOUDOPS_GITHUB_TOKEN"),
        (403, {}, "forbidden", "Actions: read"),
        (
            403,
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1800000000"},
            "rate_limited",
            "rate limit is used up until 2027",
        ),
        (403, {"Retry-After": "60"}, "rate_limited", "rate limit"),
        (429, {}, "rate_limited", "rate limit"),
        (404, {}, "not_found", "no workflow ci.yml in a/b"),
        (301, {"Location": "https://api.github.com/repositories/1"}, "moved", "renamed"),
        (500, {}, "unavailable", "HTTP 500"),
        (503, {}, "unavailable", "HTTP 503"),
        (418, {}, "invalid", "unexpected HTTP 418"),
    ],
)
async def test_error_responses(
    status: int, headers: dict[str, str], kind: str, fragment: str
) -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, headers=headers, json={"message": f"{TOKEN} echoed"})

    with pytest.raises(GitHubError) as caught:
        await _client(handler).workflow_runs("a/b", "ci.yml")
    assert caught.value.kind == kind
    assert fragment in caught.value.message
    assert TOKEN not in caught.value.message


async def test_redirects_are_not_followed_so_the_token_stays_put() -> None:
    requests: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(str(request.url))
        return httpx2.Response(302, headers={"Location": "https://attacker.example/steal"})

    with pytest.raises(GitHubError):
        await _client(handler).workflow_runs("a/b", "ci.yml")
    assert len(requests) == 1


@pytest.mark.parametrize(
    "error",
    [httpx2.ConnectError("refused"), httpx2.ReadTimeout("slow"), httpx2.ConnectTimeout("slow")],
)
async def test_network_failures_are_unavailable(error: Exception) -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        raise error

    with pytest.raises(GitHubError) as caught:
        await _client(handler).workflow_runs("a/b", "ci.yml")
    assert caught.value.kind == "unavailable"


@pytest.mark.parametrize(
    "body",
    [
        b"<html>not json</html>",
        b'{"unexpected": true}',
        json.dumps({"workflow_runs": [{"id": "not-a-number"}]}).encode(),
    ],
)
async def test_unreadable_responses_are_reported(body: Any) -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body)

    with pytest.raises(GitHubError) as caught:
        await _client(handler).workflow_runs("a/b", "ci.yml")
    assert caught.value.kind == "invalid"
