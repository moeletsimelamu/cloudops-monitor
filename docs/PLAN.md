# CloudOps Monitor — Implementation Plan

This is a living document: phase status, scope, the technical plan and the decision log. Each
finished phase gets a report in [`PHASE_REPORTS/`](PHASE_REPORTS/).

## Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Discovery and architecture | Done — approved 2026-10-27 |
| 2 | Backend foundation | Done — 95 tests, ruff and mypy clean |
| 3 | Application registry and monitoring | Done — 333 backend + 8 sandbox tests; live run against the sandbox |
| 4 | Events, logs and deployment records | Done — 488 backend + 17 sandbox tests; live run including a real GitHub sync |
| 5 | Frontend and product design | Done — 491 backend + 76 frontend tests; every screen checked in headless Chromium against real API data |
| 6 | CI/CD and packaging | Not started |
| 7 | Integration and operational hardening | Not started |
| 8 | Security and quality review | Not started |
| 9 | Deployment and final verification | Not started — needs a decision (see §6) |
| 10 | Portfolio readiness | Not started |

## 1. Environment (inspected 2026-10-27)

| Tool | Found | Notes |
|---|---|---|
| Python | 3.14.3 | No `uv`. Uses `venv` + `pip` with a constraints file, the same approach as DocuMind. |
| Node / npm | 22.14.0 / 10.9.2 | Node 22.14 cannot run Angular 22, so the frontend uses **Angular 21.2**. |
| Angular CLI | 21.2.13 (global) | The project pins its own CLI. |
| Git | 2.54 | The new repository uses the local identity `Moeletsi Melamu <moeletsimelamu03@gmail.com>`. |
| Docker | **not installed** | The image and Compose file can only be built and smoke-tested in GitHub Actions. Local Docker claims will not be made. |
| GitHub CLI | **not installed** | The GitHub repository has to be created by the developer on github.com. Pushing works through Git Credential Manager. |
| WSL, cloud CLIs (az, aws, gcloud, fly) | not installed | No cloud account is available to this session. |

The workspace is the developer's Desktop. OpsHub (Project 1) and DocuMind (Project 2) are separate
repositories. CloudOps Monitor gets **its own repository** at `Desktop/CloudOpsMonitor` and does not
touch either of them. They are potential *monitoring targets* through their existing health
endpoints: OpsHub `GET /health` on `localhost:5050`; DocuMind `GET /api/health` and `/api/ready` on
`localhost:8000`.

## 2. First-release scope

### In scope

1. Application registry with validation and an SSRF-safe target policy (allowlist by default).
2. A monitoring worker: scheduled HTTP checks, timeouts, classification, a state machine with
   hysteresis, staleness, incidents, retention, a heartbeat, and gap detection after restarts.
3. Health history: stored checks, response-time series, availability with coverage, and state
   transitions.
4. Operational events on transitions only, with no duplicate alerts.
5. Deployment history pulled from GitHub Actions workflow runs (a read-only token, or none for
   public repositories).
6. Structured log ingestion (per-application keys, redaction, limits), plus a filterable log
   explorer and request-error counts derived from request logs.
7. Overview, Applications, Application detail (evidence timeline), Health, Deployments, Logs,
   Events, System and Sign-in screens.
8. Liveness and readiness endpoints and a System status view.
9. Single-operator token authentication (session cookie). A disabled mode for local development.
10. A **sandbox service**: a controllable local test target used for the demo, the smoke test and
    end-to-end tests.
11. Tests (unit, API, frontend, end-to-end), a Dockerfile, a Compose file (CloudOps + sandbox), and
    GitHub Actions CI and deploy workflows.

### Out of scope for the first release

Notifications (email, Slack, webhooks) · triggering deployments from the UI · multiple users and
roles · SSO · flapping detection · multi-region probing · non-HTTP checks (TCP, ping, DNS,
certificate expiry) · log full-text indexing beyond `LIKE` · Kubernetes · message brokers ·
PostgreSQL · AI recommendations · multi-tenancy.

There is **no demonstration mode with simulated data.** The sandbox produces *real* observations
of a real local process whose behaviour you control, and that is clearer than labelled fake data.
Deployment records come only from GitHub. When no repository is configured, the UI says so.

## 3. Folder structure

```
CloudOpsMonitor/
├── backend/
│   ├── pyproject.toml            # direct deps pinned; ruff, mypy, pytest config
│   ├── constraints.txt           # full transitive pin set
│   ├── alembic.ini
│   ├── src/cloudops/
│   │   ├── main.py               # app factory, lifespan (starts worker), static files
│   │   ├── config.py  logging.py  cli.py
│   │   ├── api/                  # routers: applications, checks, incidents, events,
│   │   │                         #   deployments, logs, ingest, overview, system, auth
│   │   ├── domain/               # pure logic: classification, state, availability,
│   │   │                         #   redaction, deployment_status
│   │   ├── monitoring/           # target_policy, probe (pinned transport), scheduler,
│   │   │                         #   heartbeat, retention
│   │   ├── deployments/          # github_client, sync
│   │   ├── ingest/               # log batch validation + storage
│   │   └── db/                   # models, session, repositories, migrations/
│   └── tests/
│       ├── unit/                 # domain + policy + probe with mock transport
│       ├── integration/          # API tests on a temp SQLite DB, real local HTTP targets
│       └── conftest.py
├── sandbox/                      # controllable test target (FastAPI) + log shipper
│   ├── pyproject.toml
│   └── src/cloudops_sandbox/
├── frontend/                     # Angular 21 workspace
│   ├── src/app/
│   │   ├── core/                 # api client, auth, time-range + clock services, interceptors
│   │   ├── shared/               # status badge, time display, data table, charts/, empty-state
│   │   └── features/             # overview, applications, application-detail, health,
│   │                             #   deployments, logs, events, system, sign-in
│   └── e2e/                      # Playwright: smoke journey + axe checks
├── scripts/
│   └── smoke.py                  # API-level primary-workflow smoke test (uses the sandbox)
├── .github/workflows/
│   ├── ci.yml                    # every push / PR
│   └── deploy.yml                # manual + main; deploys only when configured
├── Dockerfile                    # frontend build → python runtime, non-root
├── compose.yaml                  # cloudops + sandbox
├── .env.example
└── docs/  ARCHITECTURE · DESIGN · SECURITY · DEPLOYMENT · REVIEW · PLAN · PHASE_REPORTS/
```

## 4. Dependencies and versions

Direct dependencies are pinned exactly. Versions start from the set already proven together on
this machine and in CI for DocuMind (October 2026). New packages were checked against the package
indexes on 2026-10-27.

**Backend (Python ≥ 3.12; developed on 3.14.3, image `python:3.14-slim`)**

| Package | Version | Why |
|---|---|---|
| fastapi | 0.162.0 | API framework |
| uvicorn | 0.54.0 | ASGI server |
| pydantic / pydantic-settings | 2.14.1 / 2.16.1 | validation, typed config |
| sqlalchemy | 2.1.6 | ORM / query layer |
| alembic | 1.20.1 | schema migrations (new for this project) |
| httpx2 | 2.16.0 | async HTTP client for probes and GitHub; maintained successor to httpx, required by Starlette 1.10's test client. Phase 3 confirms it supports a custom network backend for pinned connections; if not, the probe uses a custom transport at the httpcore layer. |
| *dev:* pytest 9.1.1, ruff 0.17.2, mypy 2.4.0, pip-audit (Phase 6) | | tests, lint, types, dependency audit |

No mocking library: probe tests use `MockTransport` and real throwaway local HTTP servers
(including deliberately slow ones for timeout tests).

**Frontend (Node 22.14, npm 10.9)**

| Package | Version | Why |
|---|---|---|
| @angular/* | 21.2.25 (CLI/build 21.2.28) | framework, standalone components, signals |
| rxjs / tslib / typescript | 7.8.2 / 2.8.1 / 5.9.3 | |
| @fontsource/ibm-plex-sans, @fontsource/ibm-plex-mono | 5.3.0 | self-hosted fonts |
| vitest + jsdom | 4.1.12 / 28.1.0 | unit and component tests |
| @playwright/test + @axe-core/playwright | 1.64.0 / 4.13.0 | end-to-end tests and accessibility checks |
| prettier | 3.9.10 | formatting check |
| override `vite` | 7.3.7 | npm 10.9 crashes on the newest Vite peer range (DocuMind finding) |

No charting library and no UI component library (see ARCHITECTURE §10).

## 5. Testing plan

| Layer | What | How |
|---|---|---|
| Unit (backend) | Classification table (every row of ARCHITECTURE §3.3); state machine incl. hysteresis, thresholds and the `unknown` start; incident open/escalate/close; staleness; availability and coverage with gaps and pauses; percentiles; redaction patterns; GitHub status mapping; target policy (each blocked range, IPv4-mapped IPv6, ports, user-info, allowlist matching); scheduling and jitter | pytest, pure functions, table-driven |
| Probe | Real local servers returning 200/3xx/404/429/500/503, slow responses (timeout), closed ports (connection refused), unresolvable names, oversized bodies, JSON `status` bodies, a DNS answer changing to a blocked address | pytest + a throwaway uvicorn server on an ephemeral port + resolver injection |
| API integration | Register / validate / update / delete; run check now; health history and availability endpoints; events; deployments sync against a fake GitHub (MockTransport with recorded-shape payloads); log ingest (auth, limits, redaction, per-entry errors) and filtering; pagination and time-range validation; auth on/off, sessions, Origin check; liveness vs readiness (worker stopped → not ready); error envelope never leaks internals | pytest + TestClient + temporary SQLite file per test |
| Worker | Concurrency cap; a slow target does not block others; restart → `worker_started` event with gap; retention batches | pytest with a controllable clock |
| Frontend | Registry renders; status badge shows glyph + label for each state; detail loads and shows partial failures per panel; deployment filters; log filters and empty results; API error display; buttons disabled while pending; time-range query params | vitest + Angular testing utilities, HTTP testing controller |
| End-to-end | The primary journey against real processes: start CloudOps + sandbox → register the sandbox → healthy → switch the sandbox to `errors` → degraded + incident + event + error logs → restore → recovered; axe has no serious violations on each screen | Playwright (`frontend/e2e`) and `scripts/smoke.py` (API only, runnable without a browser) |
| CI | ruff, mypy, pytest, prettier, frontend tests, production build, Playwright smoke, Docker build + container smoke (`/api/health/ready`) | GitHub Actions |

Results are recorded in the phase reports from real command output. Counts are never estimated.

## 6. Deployment strategy, cost and decision needed

### What a monitor needs from a host

An **always-on process** (the scheduler must keep running) and **persistent disk** (SQLite). This
rules out scale-to-zero platforms unless you pay to keep an instance running. It also rules out
platforms with ephemeral file systems unless you add a managed database, which this project does
not need.

### Options (pricing must be re-checked on the provider's pages at Phase 9; free tiers change)

| Option | Fit | Cost risk | Notes |
|---|---|---|---|
| **A. Local + container image published to GitHub Container Registry** | Always works; reproducible | None for a public package | The baseline. `deploy.yml` builds, tests, pushes the image and smoke-tests it in the runner. |
| **B. One small Linux VM running Docker Compose behind Caddy (automatic HTTPS)** | Best technical fit: always-on, a disk volume, full control | Low to moderate. Some providers have always-free tiers, which are region-restricted and subject to change. Others cost a few USD a month. Watch public IP, disk and egress charges. Student credit programmes may apply, but would need checking. | `deploy.yml` deploys over SSH with a deploy-only key stored as a GitHub environment secret, then verifies `/api/health/ready`. Teardown: delete the VM, disk, IP and DNS record, and revoke the key. |
| C. Container PaaS (Cloud Run, Azure Container Apps, Render, Fly.io) | Poor without paid always-on instances and volumes | Free tiers usually scale to zero (which stops the worker) and have no persistent disk | Rejected unless paying for a minimum instance + volume. |
| D. Kubernetes / managed monitoring | Not justified | High | Out of scope by design. |

**Recommendation:** build and verify with **Option A**. Write `deploy.yml` so that it also
supports **Option B** when, and only when, the `DEPLOY_HOST`, `DEPLOY_USER` and `DEPLOY_SSH_KEY`
environment secrets exist. Without them, the deploy job prints which configuration is missing and
ends with a neutral result. It never reports a deployment that did not happen. Whether to actually
create a VM is your decision at Phase 9, after a current price check. No resources will be created
without your approval.

### How deployment records work in the local demo

The sandbox application is linked to this repository's `deploy.yml`, so its deployment history
shows the **real** runs of that workflow from GitHub. With Option A, a "deployment" means the
sandbox image was built, tested, published and smoke-tested. That is an honest description and is
how the UI labels it. With Option B, it also means the image was rolled out to the VM.

## 7. Phases and completion criteria

A phase is complete only when every "done when" item has been checked by running something.

**2 — Backend foundation.** App factory, settings (+ warnings), JSON logging with request IDs and
redaction, error envelope, SQLite engine and PRAGMAs, Alembic baseline migration, all tables,
`/api/health/live`, `/api/health/ready`, security headers, auth (token mode + sessions + Origin
check), ruff + mypy + pytest configuration, pinned dependencies and constraints, `.env.example`.
*Done when:* pytest passes for live/ready (incl. DB down → 503), migrations up from empty, error
envelope (validation, 404, 500 without leaks), auth on/off/session expiry/Origin; ruff and mypy are
clean; `uvicorn` starts.

**3 — Registry and monitoring.** Target policy, application CRUD + pause/resume, probe with pinned
connections, classification, state machine, incidents, staleness, scheduler + heartbeat +
concurrency cap, run-check-now, health history, availability/latency endpoints, retention, and
the sandbox service.
*Done when:* unit and probe tests cover every classification row and blocked range; a slow target
doesn't delay others (asserted timing); a manual run against the sandbox shows healthy →
degraded → recovered with exactly one incident and three events.

**4 — Events, logs, deployments.** Events API, log ingest + redaction + limits + query, request
metrics, the sandbox log shipper, GitHub client (ETag, rate limit), sync job, deployment API,
correlation note, and the overview aggregate endpoint.
*Done when:* tests cover ingest auth and limits, every redaction rule, log filters + pagination,
sync idempotency and the status mapping (fake GitHub), sync failure without repeated events; one
real sync of a public repository's workflow runs is performed and its output recorded.

**5 — Frontend.** Shell, all screens of DESIGN §5, the evidence timeline, all states of DESIGN §7,
and themes.
*Done when:* vitest passes; each screen is inspected in the browser at 1440/1024/390 px in both
themes against real API data; the console is clean; keyboard pass on the primary journey.

**6 — CI/CD and packaging.** Dockerfile, compose.yaml, ci.yml, deploy.yml (Option A + guarded B),
pip-audit/npm audit, DEPLOYMENT.md.
*Done when:* CI is green on GitHub (needs the repository, see §8), the image builds and passes the
container smoke test in CI, and deploy.yml runs and reports "not configured" correctly for B.

**7 — Integration hardening.** The smoke script and Playwright journey; worker interruption and
restart; database locked or unavailable; invalid configuration at start-up; clock and timezone
handling; retention under load (seeded 30 days of checks).
*Done when:* every bullet in brief §Phase 7 has a test or a recorded manual run.

**8 — Security and quality review.** SSRF review (incl. rebinding test), auth review, secret
scanning of history, redaction, container configuration, dependency audit, accessibility (axe +
manual), responsive layouts; complete SECURITY.md §10 and REVIEW.md.

**9 — Deployment.** Option A always; Option B only with approval and credentials.

**10 — Portfolio readiness.** Final README, real screenshots (sandbox and fictional application
names only), verification of every claim, final report.

## 8. Open items and blockers

| # | Item | Blocks | Needed from you |
|---|---|---|---|
| B1 | Resolved 2026-11-05: `moeletsimelamu/cloudops-monitor` exists. `gh` is not installed, so CI results are read on github.com | — | Nothing. |
| B2 | Docker is not installed locally | Local `docker compose up` verification | Nothing. The image is verified in CI. Installing Docker Desktop later would allow local verification. |
| B3 | Cloud hosting choice | Phase 9 Option B only | A decision at Phase 9, after a price check. |

## 9. Decision log

| # | Decision | Reason |
|---|---|---|
| D1 | Separate repository at `Desktop/CloudOpsMonitor` | Keeps the OpsHub and DocuMind histories untouched; one repository per portfolio project. |
| D2 | Modular monolith, in-process asyncio worker | Single SQLite writer, small workload, simplest to run and explain; the module boundary allows a split later. |
| D3 | SQLite + SQLAlchemy 2.1 + Alembic | Local-first, single file to back up; Alembic shows managed schema evolution. |
| D4 | Target allowlist by default; public mode optional; link-local/metadata always blocked; pinned connections; no redirects | SSRF is the main risk of a URL monitor. |
| D5 | Four signals (ok/warn/fail/inconclusive) → four states with F/R hysteresis; staleness computed on read | Separates "app is broken" from "monitor can't see"; old checks never look healthy. |
| D6 | 500 → degraded, 502/503/504/network → unavailable | The process answering with errors differs from not serving at all; matches the product scenario. |
| D7 | Events only on transitions; one open incident per application enforced by a partial unique index | No duplicate alerts by construction. |
| D8 | Deployments pulled from the GitHub REST API with an optional read-only fine-grained token | Works without exposing CloudOps publicly; GitHub stays the source of truth. |
| D9 | No deployment trigger in the first release | Avoids a write-scoped credential and an auth surface for little demo value. |
| D10 | Logs pushed with per-application ingest keys; redaction before storage | No agents or clusters; keys prevent cross-application writes. |
| D11 | No simulated-data mode; a controllable sandbox target instead | Real observations beat labelled fake data. |
| D12 | Operator token → HttpOnly SameSite=Strict session; disabled mode for local development only | Protects operations once reachable beyond localhost without building user management. |
| D13 | Angular 21.2, hand-written SVG charts, no UI kit | Node 22.14 constraint; small, accessible, distinctive UI. |
| D14 | Option A (GHCR image) as the baseline deployment; VM over SSH only if approved | Zero-cost and verifiable; avoids PaaS that cannot keep a worker and disk. |
| D15 | The application engine keeps the sqlite3 driver's default transactions; the migration engine uses real `BEGIN IMMEDIATE` around DDL with foreign keys off | The app avoids "database is locked" when a read snapshot is upgraded to a write; migrations are all-or-nothing, and batch table re-creation cannot cascade deletes. |
| D16 | All tables created in the Alembic baseline, including those used only from Phase 3/4 | One reviewed baseline. Later phases add columns through new revisions, which exercises the migration path for real. |
| D17 | Start-up refuses a missing, short or `.env.example` placeholder operator token; blank secrets count as unset | A copied example file must not run with a known token, and an empty variable must not show as "set". |
| D18 | The log handler resolves `sys.stdout` at write time | Found in testing: a handler bound at start-up wrote to a closed stream once the runner replaced stdout. |
| D19 | Readiness reads `alembic_version` directly; head revision cached | Alembic's MigrationContext logs at INFO on each call, and readiness is polled often. |
| D20 | The probe uses httpcore2's connection pool directly, with a custom network backend | httpx2's transport does not expose the network backend; httpcore2's is public API. Also means no proxy settings from the environment. |
| D21 | Several resolved addresses are raced 250 ms apart (RFC 8305) | Found in the live run: sequential attempts added ~2 s per `localhost` check on Windows (`::1` refused slowly), so a healthy app looked degraded. A regression test fails with sequential attempts (2,047 ms) and passes now. |
| D22 | `last_check_at` renamed to `last_observed_at` (conclusive checks) and `last_attempt_at` added; the migration was hand-edited | Freshness must ignore checks the monitor could not complete. Autogenerate proposed drop-and-add, which loses data; a test covers upgrade and downgrade with data. |
| D23 | Reading the registry and history also needs the operator | Configuration and incident history are sensitive once the server is reachable by others. |
| D24 | One blocked address among several blocks the check | Conservative: a name that points anywhere forbidden is suspect even if another address is fine. |
| D25 | "Check now" shares the scheduler's in-flight set (409 while running) and is spaced 5 s per application (429) | One check at a time per application keeps the state machine's ordering correct; spacing stops the button being used as a load generator. |
| D26 | The sandbox is a separate small package (`sandbox/`) with the same pinned FastAPI/uvicorn | It is a test target, not part of the product; keeping it separate keeps the product image free of it. |
| D27 | Database work on the event loop goes through `run_db_work`, which finishes the work before delivering cancellation | anyio's shielding does not cover native `task.cancel()`: `stop()` could return while a thread was writing, and the engine was disposed under it. |
| D28 | Logs, events and deployments page by an opaque (time, ID) cursor | Late log entries and backfilled runs mean IDs do not follow time order, so an ID cursor would skip or repeat rows. |
| D29 | Ingest is limited per request (256 KiB, 500 entries) and per application per minute | Retention is hourly; without a rate limit, one noisy sender could fill the disk in between. |
| D30 | Rejection messages for log entries never repeat the input | A rejected entry may be exactly the secret that should not be stored or echoed. |
| D31 | The GitHub client follows no redirects and ignores environment proxies | The token must only reach the configured API host. |
| D32 | A run first seen already finished records only its outcome event, at GitHub's time | Backfilled runs stay accurate on the timeline without inventing a "started" event seen at the wrong moment. |
| D33 | Correlation prefers a deployment running at the time, else the latest to finish within 60 min, and says "after", never "caused by" | Timing is evidence; it is not a diagnosis. |
| D34 | The test HTTP server runs in its own process | In-process, its sockets were collected at random and looked like leaks in unrelated tests. Apart, the warning filters could be removed, so any unclosed socket in the test process now fails a test and is the probe's own. |
| D35 | A garbage collection runs after every test | A leak then fails the test that caused it, rather than whichever test is running when the collector next wakes. |
| D36 | Styles shared between components live in a stylesheet (`status-badge.css`), not a shared string constant | With one inline `styles` constant used by four components, `ng build` finished its work and then never exited, held open by an esbuild child process. Moving it to `styleUrl` fixed that. The same toolchain versions behaved in DocuMind, which ruled out an install problem. The exact mechanism inside the builder was not established. |
| D37 | Shell tests go through the real router | A crash on every page got past unit tests that rendered the shell directly, because a child route has no snapshot while the router is still activating it. It was found only by running the app. |
| D38 | Status colours were chosen with a palette validator | The Phase 1 values were too close under simulated colour blindness. See DESIGN §3. |
| D39 | Deployment lists ignore the page's time range, and `GET /api/deployments` has no default range | 13 real runs were stored, but the page said there were none, because the newest was 9 days old. Selecting a period on the timeline still narrows the list on the application page. |
| D40 | No custom time range; logs load in pages rather than a virtualised list | Neither was needed once "Logs ±10 min" links existed and pages were capped at 100 rows. Both add code without answering a new question. |
| D41 | The timeline fits a phone rather than scrolling inside its panel | Scrolling started at the oldest data, so the newest was off-screen, which is backwards for a monitor. |
| D42 | Phase 5 browser checks are scratch scripts; they move into the repository as the Playwright journey in Phase 7 | They were needed to verify this phase but depend on a hand-prepared database. Phase 7 makes them repeatable. |
