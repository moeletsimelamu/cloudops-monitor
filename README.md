# CloudOps Monitor

A deployment and application-health monitoring platform for a developer or small team. It brings
together health checks, deployment history, structured logs and operational events in one place,
so that when something breaks you can see **what is broken, since when, what changed and what the
evidence says**.

> **Project status: Phase 7 of 10.** The backend and the web interface work together locally: it
> checks application health on a schedule, tracks state and incidents, records events, ingests and
> searches structured logs, reads deployment history from GitHub Actions, and shows all of it in an
> Angular interface. It is tested against a controllable sandbox service, as a running system
> (restarts, kills, a locked database, 40 days of data) and through a browser, packaged as a
> container image, and checked and published by GitHub Actions. It has not been deployed to a
> server.
> Progress is tracked in [docs/PLAN.md](docs/PLAN.md).

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

## Features

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
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Running the container image and Compose setup, operating it, the CI/CD pipeline, the server option |

## Running it with Docker

With Docker installed, CloudOps Monitor and the sandbox start together. Put two random values in a
file named `.env` next to `compose.yaml`:

```
CLOUDOPS_OPERATOR_TOKEN=<at least 32 random characters>
SANDBOX_CONTROL_TOKEN=<another random value>
```

```bash
docker compose up --build -d
```

Then open http://localhost:8300, sign in with the operator token and register
`http://sandbox:8310/health`. [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) covers the image on its own,
backups, upgrades and the pipeline.

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
.venv\Scripts\cloudops serve --port 8300
```

A setting it cannot use stops it at once with one log line naming the variable (exit status 2).

Checks used during development and in CI. The full suite includes system tests that start the real
server as a separate process (restarts, a locked database, retention on 1.15 million checks) and
takes about five minutes; `-m "not system"` leaves them out:

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

## Development setup (frontend)

Requires Node.js 22. With the API running on port 8300, this serves the interface on
http://localhost:4300 and passes `/api` requests through to the API:

```powershell
cd frontend
npm ci
npm start
```

Sign in with the operator token. Checks used during development:

```powershell
npm test
```

```powershell
npm run format:check
```

```powershell
npm run build
```

Browser end-to-end tests (Playwright) run the production build against the real server and the
sandbox, each started for the run with an empty database. They need the backend and the sandbox
installed in `backend/.venv` (or the Python named in `E2E_PYTHON`) and a build:

```powershell
npx playwright install chromium
```

```powershell
npm run build; npm run e2e
```

## Trying it with the sandbox

The sandbox (`sandbox/`) is a small service whose health you switch on purpose. CloudOps Monitor
then observes real HTTP responses from a real process. Install it into the backend's environment
and run it on http://127.0.0.1:8310:

```powershell
.venv\Scripts\python -m pip install -e ..\sandbox
.venv\Scripts\python -m cloudops_sandbox
```

With `CLOUDOPS_ALLOWED_TARGETS=localhost:8310` in `backend/.env`, register it under **Applications**
in the interface with the health URL `http://localhost:8310/health`, or through the API, and then
break it (any HTTP client works; these use PowerShell):

```powershell
$h = @{ Authorization = "Bearer <your operator token>" }
Invoke-RestMethod -Method Post http://localhost:8300/api/applications -Headers $h -ContentType application/json -Body '{"slug":"sandbox-orders","name":"Sandbox orders","environment":"demo","check_url":"http://localhost:8310/health","interval_seconds":15}'
Invoke-RestMethod -Method Put http://localhost:8310/control/mode -ContentType application/json -Body '{"mode":"errors"}'
```

Within two checks the application turns **degraded**, an incident opens and an event is recorded.
Switch back with `{"mode":"healthy"}` to see it recover. The sandbox's modes are `healthy`,
`degraded`, `errors`, `slow`, `unavailable` and `flaky`.

To see logs and failing requests as well, create an ingest key for the application and restart the
sandbox with it. `SANDBOX_TRAFFIC_RPS` makes it call its own API, so there are requests to observe;
those requests are real but generated, and are logged with `synthetic=true`:

```powershell
$key = (Invoke-RestMethod -Method Post http://localhost:8300/api/applications/sandbox-orders/ingest-key -Headers $h).ingest_key
$env:SANDBOX_CLOUDOPS_URL = "http://localhost:8300"
$env:SANDBOX_INGEST_KEY = $key
$env:SANDBOX_TRAFFIC_RPS = "2"
.venv\Scripts\python -m cloudops_sandbox
```

Then open the application in the interface: the evidence timeline shows the incident, the failing
checks and the 5xx responses on one time axis, with the logs one tab away.

To record deployments, set `github_repo` and `deploy_workflow` on an application. CloudOps reads that
workflow's runs from GitHub every 5 minutes. Public repositories need no token, but share GitHub's
limit of 60 requests an hour per IP address, so a network with other GitHub API traffic can use it
up. A fine-grained token with *Actions: read* (`CLOUDOPS_GITHUB_TOKEN`) avoids that. CloudOps only
ever reads from GitHub: it cannot start a deployment.

Running it on a server is not set up yet; [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) says what it would take.
