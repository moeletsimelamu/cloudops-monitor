"""A small read-only client for GitHub Actions workflow runs.

Redirects are not followed and environment proxy settings are not read, so the token only ever
goes to the configured API host. Errors carry a readable message and never the response body or
the token.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote

import httpx2
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from cloudops import __version__

ErrorKind = Literal[
    "not_found", "unauthorized", "forbidden", "rate_limited", "moved", "unavailable", "invalid"
]


class GitHubError(Exception):
    def __init__(
        self, kind: ErrorKind, message: str, *, rate_reset_at: datetime | None = None
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.rate_reset_at = rate_reset_at


class GitHubActor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str | None = None


class GitHubRun(BaseModel):
    """The fields used from a workflow run. GitHub sends many more."""

    model_config = ConfigDict(extra="ignore")

    id: int
    run_number: int | None = None
    run_attempt: int = 1
    name: str | None = None
    display_title: str | None = None
    head_sha: str
    head_branch: str | None = None
    event: str | None = None
    status: str
    conclusion: str | None = None
    created_at: AwareDatetime
    run_started_at: AwareDatetime | None = None
    updated_at: AwareDatetime
    html_url: str | None = None
    actor: GitHubActor | None = None


class _RunsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workflow_runs: list[GitHubRun]


@dataclass(frozen=True)
class RunsPage:
    not_modified: bool
    runs: list[GitHubRun]
    etag: str | None
    rate_remaining: int | None
    rate_reset_at: datetime | None


def _rate_limit(headers: httpx2.Headers) -> tuple[int | None, datetime | None]:
    remaining = headers.get("x-ratelimit-remaining")
    reset = headers.get("x-ratelimit-reset")
    return (
        int(remaining) if remaining and remaining.isdigit() else None,
        datetime.fromtimestamp(int(reset), UTC) if reset and reset.isdigit() else None,
    )


class GitHubClient:
    def __init__(
        self,
        api_url: str,
        token: str | None,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    async def workflow_runs(
        self,
        repo: str,
        workflow: str,
        *,
        branch: str | None = None,
        etag: str | None = None,
        per_page: int = 20,
    ) -> RunsPage:
        """The most recent runs of one workflow. With ``etag``, an unchanged result is cheap."""
        owner, name = repo.split("/", 1)
        url = (
            f"{self.api_url}/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
            f"/actions/workflows/{quote(workflow, safe='')}/runs"
        )
        params: dict[str, str | int] = {"per_page": per_page}
        if branch:
            params["branch"] = branch
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"CloudOpsMonitor/{__version__}",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if etag:
            headers["If-None-Match"] = etag
        try:
            async with httpx2.AsyncClient(
                transport=self.transport,
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx2.TimeoutException:
            raise GitHubError("unavailable", "GitHub did not answer in time.") from None
        except httpx2.HTTPError as error:
            raise GitHubError(
                "unavailable", f"Could not reach GitHub ({type(error).__name__})."
            ) from None

        remaining, reset_at = _rate_limit(response.headers)
        self._raise_for_status(response, repo, workflow, remaining, reset_at)
        if response.status_code == 304:
            return RunsPage(True, [], etag, remaining, reset_at)
        try:
            runs = _RunsResponse.model_validate_json(response.content).workflow_runs
        except ValidationError:
            raise GitHubError(
                "invalid", "GitHub returned a response CloudOps cannot read."
            ) from None
        return RunsPage(False, runs, response.headers.get("etag"), remaining, reset_at)

    @staticmethod
    def _raise_for_status(
        response: httpx2.Response,
        repo: str,
        workflow: str,
        remaining: int | None,
        reset_at: datetime | None,
    ) -> None:
        status = response.status_code
        if status in (200, 304):
            return
        if status in (301, 302, 307, 308):
            raise GitHubError(
                "moved", f"{repo} has moved or been renamed; update github_repo to its new name."
            )
        if status == 401:
            raise GitHubError("unauthorized", "GitHub rejected CLOUDOPS_GITHUB_TOKEN.")
        if status == 404:
            raise GitHubError(
                "not_found",
                f"GitHub has no workflow {workflow} in {repo}. Check the names; a private "
                "repository also needs a token with access to it.",
            )
        if status == 429 or (
            status == 403 and (remaining == 0 or "retry-after" in response.headers)
        ):
            until = f" until {reset_at.isoformat(timespec='minutes')}" if reset_at else ""
            hint = (
                "" if "authorization" in response.request.headers else " Setting a token raises it."
            )
            raise GitHubError(
                "rate_limited",
                f"GitHub's rate limit is used up{until}.{hint}",
                rate_reset_at=reset_at,
            )
        if status == 403:
            raise GitHubError(
                "forbidden",
                f"GitHub refused access to {repo}. The token needs 'Actions: read' on it.",
            )
        if status >= 500:
            raise GitHubError("unavailable", f"GitHub answered with HTTP {status}.")
        raise GitHubError("invalid", f"GitHub answered with unexpected HTTP {status}.")
