# CloudOps Monitor

A deployment and application-health monitoring platform for a developer or small team. It brings
together health checks, deployment history, structured logs and operational events in one place,
so that when something breaks you can see **what is broken, since when, what changed and what the
evidence says**.

> **Project status: Phase 3 of 10.** The backend registers applications, checks their health on a
> schedule, tracks state and incidents, and serves health history and metrics. It is tested
> against a controllable sandbox service. Deployments, logs and the web interface are not built
> yet. Features are listed as they are implemented and verified. Progress is tracked in
> [docs/PLAN.md](docs/PLAN.md).

This is the third project in a portfolio, after
[OpsHub](https://github.com/moeletsimelamu/opshub) (IT service desk, ASP.NET Core + Angular) and
[DocuMind](https://github.com/moeletsimelamu/documind) (document knowledge assistant, FastAPI +
Angular). It focuses on operations: monitoring, diagnosis and delivery.

## The problem

A developer ships a new version. Minutes later the application starts returning errors. To find out
why, they normally jump between a CI dashboard, server logs, a status page and guesswork.
CloudOps Monitor puts the signals side by side on one time axis:

- the health check went from **healthy to degraded at 14:07**,
- the **deploy of `a1b2c3d` finished at 14:03**,
- the application's request logs show **5xx errors starting at 14:04**.

## Planned features (first release)

- **Application registry** with validated health-check URLs and an SSRF-safe target allowlist.
- **Health monitoring**: scheduled HTTP checks with timeouts, a documented classification of
  responses and network failures, a healthy / degraded / unavailable state machine with hysteresis,
  and staleness detection, so an old success never looks current.
- **Health history**: response times, failed checks, state transitions, and availability with
  coverage shown alongside.
- **Incidents and events** recorded on state transitions only, with no repeated alerts for a
  failure that persists.
- **Deployment history** read from real GitHub Actions workflow runs.
- **Structured log ingestion and a log explorer** with per-application keys, redaction and filters.
- **A troubleshooting workspace** per application that combines all of the above on one evidence
  timeline.
- **System status**: liveness, readiness, worker heartbeat and non-secret configuration.
- **A sandbox service**: a controllable local target you can switch between healthy, erroring, slow
  and down. It is used for the demo and the automated end-to-end tests.

## Architecture in brief

A modular monolith: one FastAPI process serves the API and the built Angular app, and runs the
monitoring worker as an asyncio task. SQLite stores everything. Applications push logs to an
ingest endpoint. Deployment records are pulled from the GitHub REST API. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

| Area | Technology |
|---|---|
| API and worker | Python 3.14, FastAPI, Pydantic, SQLAlchemy 2.1, Alembic, httpx2 |
| Database | SQLite (WAL) |
| Frontend | Angular 21, TypeScript, hand-written SVG charts, IBM Plex fonts |
| Tests | pytest, Vitest, Playwright + axe |
| Delivery | Docker, Docker Compose, GitHub Actions, GitHub Container Registry |

## Documentation

| Document | Contents |
|---|---|
| [docs/PLAN.md](docs/PLAN.md) | Scope, folder structure, dependency versions, testing plan, deployment options and costs, decision log |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, health-check classification and state machine, events, logs, deployments, data model |
| [docs/DESIGN.md](docs/DESIGN.md) | Interface principles, visual identity, screens, accessibility, responsive behaviour |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, SSRF controls, authentication, secrets, logging hygiene |

## Development setup (backend)

Requires Python 3.12 or newer. Commands are shown for Windows (PowerShell). On Linux or macOS, use
`.venv/bin/` instead of `.venv\Scripts\`.

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -c constraints.txt -e ".[dev]"
```

Configure, then start the API on http://127.0.0.1:8300 (migrations run automatically):

```powershell
copy ..\.env.example .env
.venv\Scripts\cloudops generate-token
```

Put the generated token in `CLOUDOPS_OPERATOR_TOKEN` in `backend/.env`, then:

```powershell
.venv\Scripts\python -m uvicorn cloudops.main:create_app --factory --port 8300
```

Checks used during development and in CI:

```powershell
.venv\Scripts\python -m pytest
```

```powershell
.venv\Scripts\ruff check .
```

```powershell
.venv\Scripts\mypy
```

Health endpoints: `GET /api/health/live` (process up) and `GET /api/health/ready` (database and
schema usable). API documentation is at `/api/docs` while `CLOUDOPS_API_DOCS=true`.

## Trying it with the sandbox

The sandbox (`sandbox/`) is a small service whose health you switch on purpose. CloudOps Monitor
then observes real HTTP responses from a real process. Install it into the backend's environment
and run it on http://127.0.0.1:8310:

```powershell
.venv\Scripts\python -m pip install -e ..\sandbox
.venv\Scripts\python -m cloudops_sandbox
```

With `CLOUDOPS_ALLOWED_TARGETS=localhost:8310` in `backend/.env`, register it and break it (any
HTTP client works; these use PowerShell):

```powershell
$h = @{ Authorization = "Bearer <your operator token>" }
Invoke-RestMethod -Method Post http://localhost:8300/api/applications -Headers $h -ContentType application/json -Body '{"slug":"sandbox-orders","name":"Sandbox orders","environment":"demo","check_url":"http://localhost:8310/health","interval_seconds":15}'
Invoke-RestMethod -Method Put http://localhost:8310/control/mode -ContentType application/json -Body '{"mode":"errors"}'
```

Within two checks the application turns **degraded**, an incident opens and an event is recorded.
Switch back with `{"mode":"healthy"}` to see it recover. The sandbox's modes are `healthy`,
`degraded`, `errors`, `slow`, `unavailable` and `flaky`.

Docker, frontend, CI/CD and deployment instructions will be added as each part is built and
verified.
