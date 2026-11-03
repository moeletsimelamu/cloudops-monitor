# Phase 3 — Application registry and monitoring

**Dates:** 2026-10-27 to 2026-10-28

## What was built

| Area | Implementation (`backend/src/cloudops/…`) |
|---|---|
| Classification | `domain/classification.py`: every row of ARCHITECTURE §3.3, including the JSON `status` field (`pass`/`warn`/`fail` vocabulary), protocol errors and unexpected statuses. Details are bounded to 300 characters. |
| State and freshness | `domain/health_state.py`: state machine with failure and recovery thresholds, incident transitions, and staleness computed on read (3 × interval, at least 2 min). |
| Availability | `domain/availability.py`: availability (ok + warn over conclusive checks), coverage against monitored time minus pauses, nearest-rank p50/p95, buckets that leave gaps empty, and a refusal to give a percentage with fewer than 10 checks or under 50% coverage. |
| Target policy | `monitoring/target_policy.py`: URL rules, the allowlist, public mode, always-blocked ranges (link-local, metadata services, reserved, multicast), and IPv4-mapped unwrapping. |
| Probe | `monitoring/probe.py`: httpcore2 pool with a pinned network backend, Happy Eyeballs across checked addresses, no redirects, 64 KiB body cap, hard deadline, and error mapping. |
| Recording | `monitoring/recorder.py` and `events.py`: the check row, state, incident and events in one transaction. Events are written only on transitions, plus once per run of blocked checks. |
| Worker | `monitoring/runner.py`, `monitoring/worker.py`, `monitoring/retention.py`: 1 s scheduling loop, concurrency cap, one check per application at a time, jitter, heartbeat, a gap event after restarts, a loop that survives failing ticks, hourly batched retention and a log row cap. |
| API | `api/applications.py`, `api/views.py`, `api/params.py`: registry CRUD, pause/resume, check now, keyset-paged history with validated time ranges, incidents, metrics with definitions. `GET /api/system/status`. The worker is added to readiness. |
| Schema | Migration `fa05ffb1e657`: `last_check_at` renamed to `last_observed_at`, and `last_attempt_at` added. Hand-edited, because autogenerate proposed a lossy drop-and-add. |
| Sandbox | `sandbox/`: a controllable target with healthy / degraded / errors / slow / unavailable / flaky modes, and a control endpoint protected by token or loopback-only access. |

## Verification (commands actually run)

| Command | Result |
|---|---|
| `backend: python -m pytest` | **333 passed** (applications API 45, auth 13, availability 19, classification 43, cli 4, config 14, db 12, errors/middleware 12, health 6, health_state 33, logging 4, probe 22, recorder 10, redaction 30, system config 2, target policy 53, worker 11) |
| `backend: ruff check .`, `ruff format --check .`, `mypy` (strict) | Clean (61 source files) |
| `sandbox: python -m pytest`, `ruff check .`, `mypy` | 8 passed; clean |

The probe tests use real sockets: a uvicorn test server, raw TCP servers that hang up or send
garbage, closed ports, and TLS spoken to a plain-HTTP port. DNS behaviour (failure, slow
resolution, rebinding) uses an injected resolver, so no test needs the internet.

### Live run (CloudOps Monitor on 8300 with the worker on, sandbox on 8310, Windows 11)

Registered `http://localhost:8310/health` with a 15 s interval, then switched the sandbox
through its modes and used "check now":

| Sandbox mode | Checks seen | State |
|---|---|---|
| healthy | ok, HTTP 200, ~262 ms | healthy |
| errors | warn/server_error, HTTP 500 | degraded after the 2nd check (incident opened) |
| unavailable | fail/service_unavailable, HTTP 503 | escalated to unavailable after the 2nd |
| healthy | ok | recovered after the 2nd ok: "recovered after 45 s" |
| slow (3 s) | warn/slow_response, "HTTP 200 in 3279 ms" | — |
| process stopped | fail/connection_failed (~2.3 s, Windows' refusal time) | unavailable |

Events recorded: one each for degraded, escalated and recovered per incident, with no repeats
while a failure persisted. The first incident in the log (10 s) came from the worker's first
check of a sandbox still left in `errors` mode by an earlier, aborted run. It is a genuine
observation.

Restart: with the API stopped, the stored heartbeat was **backdated by 12 minutes** to simulate
downtime without waiting. On start, the worker recorded "Monitoring resumed after a 12 min gap: no
checks ran during that time" (`gap_seconds` 733). Readiness reported `worker: ok`, and
`/api/system/status` showed the loop ticking (8.7 ms lag).

## Defects found and fixed

- **Healthy `localhost` targets reported as slow and degraded (found in the live run).** Addresses
  were tried in turn. On Windows, refusing `::1` takes about 2 s before `127.0.0.1` is tried, so
  every check took about 2 s. Fixed with Happy Eyeballs (D21). The regression test fails with
  sequential attempts (2,047 ms) and passes with the fix.
- **A lossy migration.** Autogenerate's drop-and-add was replaced with a rename, and the migration
  is tested both ways with data.
- **The branch-name pattern was rejected at import.** Pydantic's regex engine has no look-around,
  so the rules moved to a validator.
- Test-only errors were also fixed: a helper produced timestamps that went backwards, and some test
  arithmetic was wrong (window edges, incident durations). These were not product defects.

## Known limitations

- Measured response times for multi-address names include the 250 ms stagger when the first
  address does not answer. A real client would see the same delay.
- Coverage uses the current interval for the whole window. A changed interval skews it
  temporarily.
- The "check now" spacing and the sign-in limiter are in memory, per process.
- Not run here: Python 3.12, Linux, IPv6 targets on a real network, Docker.

The README's PowerShell commands for the sandbox were also run as written. The worker alone (no
"check now") turned the application **degraded** with reason `server_error` within the 60-second
wait.

## Post-phase fixes (2026-11-03, found while starting Phase 4)

A baseline test run failed intermittently, so the suite was run in eight parallel copies to create
contention. That exposed two real defects and one racy test:

- **Shutdown race.** `worker.stop()` used native task cancellation, which anyio's shielding does not
  cover. A thread writing to the database kept running after `stop()` returned, so the engine could
  be disposed under it and its connection leaked (seen as an `unclosed database` warning). Database
  work now goes through `run_db_work`, which finishes the work before delivering cancellation.
- **False "not ready" at start-up.** Readiness failed between start-up and the first tick. A worker
  now counts from its start time until it has ticked; one that never ticks still fails after 30 s.
- **Racy test.** `test_the_loop_survives_a_failing_tick` stopped the worker before the loop had reset
  its failure counter.

After the fixes, eight parallel runs of the timing-sensitive tests all passed (86 tests each).
