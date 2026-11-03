# Phase 4 — Events, logs and deployment records

**Date:** 2026-11-03

## What was built

| Area | Implementation (`backend/src/cloudops/…`) |
|---|---|
| Events API | `api/events.py`: timeline with filters by application, category, type, minimum severity and time range. |
| Log ingestion | `ingest/logs.py`, `api/ingest.py`: per-application keys (shown once, stored hashed, rotate and revoke). Each entry is validated separately. Limits per entry, per batch (500 entries, 256 KiB, enforced while the body is received) and per application per minute. Secrets are masked before storage. |
| Log queries | `api/logs.py`: filters by application, minimum severity, event, correlation ID, text (with `%` and `_` literal) and time (7 days at most). |
| Request metrics | `api/views.py`: requests and 5xx responses per bucket, counted in SQL from `http.request` entries. New index and migration `cef3054d48fc`. |
| GitHub client | `deployments/github_client.py`: read-only. ETag-aware, with no redirects and no environment proxies. Each failure mode has a readable error that never includes the token. |
| Deployment sync | `deployments/sync.py`, `domain/deployment_status.py`: idempotent upserts, events at GitHub's own times, one event when a sync starts failing and one when it recovers, and no requests while rate limited. Runs from the worker every 5 minutes without holding up health checks, and on demand. |
| Deployments API | `api/deployments.py`: history with filters, source status, sync now. No route starts a deployment, and a test checks that. |
| Correlation | `domain/correlation.py`: each incident names a deployment running when it opened, or else the latest to finish in the hour before. The wording reports timing only. |
| Overview | `api/overview.py`: status counts, what needs attention (worst first), per-application 24 h availability, p95 and latest deployment, recently closed incidents, recent deployments and changes. `GET /api/incidents` lists incidents across applications. |
| Sandbox | `sandbox/…/log_shipper.py`: standard-library `logging.Handler` that batches in the background and drops rather than blocks. The sandbox logs each request with a correlation ID, logs failures, and can generate labelled synthetic traffic. |

## Verification (commands actually run)

| Command | Result |
|---|---|
| `backend: python -m pytest` | **488 passed** (applications API 45, auth 13, availability 19, classification 43, cli 4, config 14, db 12, deployment sync 15, errors/middleware 12, events API 16, GitHub client 37, health 6, health state 33, logging 4, logs API 51, overview API 33, probe 23, recorder 10, redaction 30, system config 2, target policy 53, worker 13) |
| `backend: ruff check .`, `ruff format --check .`, `mypy` (strict) | Clean (80 source files) |
| `sandbox: python -m pytest`, `ruff check .`, `mypy` | 17 passed; clean |
| Timing-sensitive tests in 6 parallel copies, 5 rounds (30 runs) | 30 of 30 clean, after the fixes below |

### Live run (CloudOps Monitor on 8300, sandbox on 8310 with 2 requests/s, Windows 11)

- **Bad-deploy scenario.** Sandbox switched to `errors`: the application went degraded, an incident
  opened, and the overview listed it under "needs attention". Request metrics rose from 0 to 66 errors
  in a minute. A failing request's two log entries (`order.load_failed`, `http.request`) shared a
  correlation ID, and filtering by that ID returned both. Switched back: recovery recorded, incident
  closed, overview cleared.
- **Real GitHub sync.** `moeletsimelamu/documind`, workflow `ci.yml`, no token. 13 real runs were
  stored (11 succeeded, 2 failed) with correct commits, branches, actors, durations and links. A later
  sync returned `not_modified` through the ETag.
- **Real rate limit.** GitHub reported the network's unauthenticated limit used up (it is shared per
  IP address). The sync failed once with a readable message and one event. The next attempt did not
  call GitHub. After the reset, the worker's scheduled sync succeeded by itself and recorded a recovery
  event.
- **No connection leak.** Over about 10 minutes and 38 checks through every sandbox mode, the API
  process held exactly 3 outgoing TCP connections and 353–357 handles. No errors in the server log.
- **README commands.** Creating a key, shipping an entry, querying logs and reading request metrics
  were run as written in PowerShell.

Not shown live: the correlation note. It needs a deployment within the hour before an incident, and
DocuMind's latest run was a week old. It is covered by tests against fake GitHub runs only.

## Defects found and fixed

- **Worker shutdown race (from Phase 3, committed separately as `ef4dcb8`).** `stop()` could return
  while a thread was still writing, so the engine was disposed under it and the connection leaked.
- **False "not ready" at start-up (also `ef4dcb8`).** Readiness failed in the instant before the first
  tick.
- **Non-numeric status codes counted as requests.** SQLite casts `"not a number"` to 0, so request
  metrics overcounted. The query now checks the JSON type.
- **Intermittent test failures from unclosed sockets.** Several explanations were tried and failed
  before measurement settled it: a leaked address pair showed one local port with two remote ports,
  which only a listening server produces. The in-process test server was at fault, not the probe. It
  now runs in its own process, and the warning filters added along the way were removed, so an unclosed
  socket in the test process fails a test and can only be the probe's. A soak test also asserts that
  no probe socket stays open after 100 probes, 75 of which end mid-exchange (body cap, TLS failure,
  timeout).

## Known limitations

- `completed_at` is the run's last update time, which is close to but not exactly when it finished.
- A token-less installation shares GitHub's 60 requests an hour with everything else on its IP address.
  In this run that allowance ran out from other traffic. A fine-grained read-only token avoids this.
- The ingest limiter and "sync now" spacing are in memory, per process, and reset on restart.
- Redaction is pattern-based. It cannot catch every secret or piece of personal data.
- The overview recomputes per request. That is fine for a handful of applications, but it has not been
  measured with many, or with 30 days of data (Phase 7).
- Not run here: Python 3.12, Linux, Docker, private repositories or a real token.
