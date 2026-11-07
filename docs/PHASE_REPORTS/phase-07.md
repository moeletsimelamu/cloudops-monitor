# Phase 7 — Integration and operational hardening

**Date:** 2026-11-07

The aim was to test CloudOps Monitor as a whole running system rather than as code: the server as a
real process that is started, stopped, killed and starved of its database, and the interface in a
real browser against a real server. Six defects were found this way and fixed.

## What was built

| Area | Implementation |
|---|---|
| `cloudops serve` | The server's command, now also the container's. It checks the settings and the database before uvicorn starts: an invalid setting ends with one log line naming every variable to fix (exit 2), an unusable database with one line naming the file and SQLite's reason (exit 3). No traceback, and the rejected value is never repeated. |
| Database failures | A request that cannot use the database gets `503 database_busy` with `Retry-After: 5` (another connection held the write lock past the 5-second busy timeout) or `503 database_unavailable`, saying nothing was changed. A shutdown is logged (`app.stopped`), and so is how long retention took. |
| Retention | Old checks are deleted application by application, so each batch uses the index, and retention runs beside the scheduler instead of holding it up. A failed run is logged and tried again an hour later. |
| Overview queries | The latest check of each application is one index lookup, not a scan of every check kept. |
| System tests | `backend/tests/system/`: 17 tests that start `cloudops serve` as a child process with its own data directory and drive it over HTTP, against target servers in their own processes. |
| Browser tests | `frontend/e2e/`: 17 Playwright tests against the production build served by the real server, with the sandbox as the monitored application and an empty database per run. They replace the Phase 5 scratch scripts (D42). |
| Smoke test | `--github-repo`: links an application to a repository's workflow, syncs it, and checks the records are real runs of that repository. |
| CI | A new *Browser end-to-end* job; the backend jobs run the system tests and report the retention measurements as notices; the container job checks that an invalid setting is refused with one JSON line, and its smoke test reads this repository's own Deploy runs with the run's read-only token. |

## The brief's Phase 7 list, and where each is verified

| Brief item | Verified by |
|---|---|
| Successful health checks | System: `test_graceful_stop_and_restart…`, `test_a_timeout_opens_an_incident…`; browser: "Check now records a real check"; smoke test |
| Failed health checks | System: timeout to `unavailable` with an incident opened for `timeout`; browser and smoke test: the sandbox returning 500s |
| Recovery after failure | System: incident closed and `health.recovered`; browser: "recovery closes the incident"; smoke test |
| Monitoring timeouts | System: a 2 s timeout against a target that answers after 6 s. The check is recorded as `fail`/`timeout` after about 2 s, and the request does not wait for the target. |
| Event history | System: the event sequence `application.registered`, `health.unavailable`, `health.recovered`, in order; browser: the incident in the Events page |
| Log filtering | System: severity, text, event, correlation ID and time range, masking, ingest key required; browser: shipped with the key the page displayed, filtered, expanded, no results |
| Deployment records | Smoke test locally and in CI: real Deploy runs of this repository read from GitHub, with run URLs, run numbers and commit SHAs. The sync logic itself is tested against a fake GitHub (Phase 4). |
| Invalid configuration | System: seven invalid settings each stop start-up with exit 2 and one JSON line, without repeating secrets or creating the database; CI does the same with the container image |
| Database failures where practical | System: a write lock held by another process (503s while reads continue; readiness fails after 30 s; everything recovers on release); a damaged database file; a data directory the server cannot write (Linux only); a backup taken while running is consistent |
| Monitoring worker interruptions | Unit: a failing tick, a failing retention run, and retention no longer blocking checks; system: the worker stalled by the database lock and recovering |
| Restart behaviour | System: graceful stop logs a clean shutdown and keeps all data; a kill during an in-flight check leaves an intact database (integrity and foreign-key checks), the interrupted check is not recorded, the restart after a real 22-second outage is recorded as a gap, and the overdue application is checked straight away |
| Clock and time zone (PLAN §7) | System: a server in UTC+14 returns UTC timestamps; ranges given with an offset select the same instants; a time without an offset is refused. Browser: a UTC+14 browser shows local times, or UTC on request, and keeps the choice. |
| Retention under load (PLAN §7) | System: below |

## Verification

### On this machine (Windows 11, Python 3.14, Node 22.14)

| Command | Result |
|---|---|
| `backend: python -m pytest -m "not system"` | **514 passed** in 147 s |
| `backend: python -m pytest -m system` | **16 passed, 1 skipped** in 174 s. The skipped test needs POSIX permissions (an unwritable directory); it runs in CI. |
| `ruff check`, `ruff format --check`, `mypy` (also `mypy --platform linux`) | Clean |
| `frontend: npm test` | 76 passed |
| `frontend: npm run e2e` (after `npm run build`) | **17 passed** in 2.5 min |
| `npx tsc -p e2e/tsconfig.json`, `npm run format:check` | Clean |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `scripts/smoke.py --github-repo moeletsimelamu/cloudops-monitor` against `cloudops serve` and the sandbox | Passed in 20 s; 3 deployment records read from GitHub; every server log line was JSON |

### Retention under load

The test seeds 40 days of checks for 10 applications at 30-second intervals (1,152,010 rows,
124 MB), starts the server, and reads `/api/overview` and a checks page from two threads while
retention deletes the 10 days beyond the 30-day limit. Measured here:

| | Before the fix | After |
|---|---|---|
| Checks deleted | 288,120 | 288,120 (exactly the expected rows; none newer) |
| Retention duration | 18.0 s, with no check scheduled meanwhile | 7.1 to 7.5 s, beside the scheduler |
| API reads during retention | all 200; median 565 ms, max 2.7 s | all 200; median 299 to 399 ms, p95 about 0.9 s, max 2.6 to 2.7 s |
| API reads afterwards | not measured | median 452 to 615 ms, max 0.8 s |

The reads are slow at this size whether or not retention is running (see the limitations). The
slowest read in each run was the first, taken while the server was still starting.

### On GitHub

| Run | Result |
|---|---|
| CI #6 (branch `phase-7-hardening`, 36ae1c5) | Both backend jobs failed at the type check: `subprocess.CREATE_NEW_PROCESS_GROUP` exists only on Windows, and mypy checks per platform. Fixed; `mypy --platform linux` is now run locally as well. |
| CI #7 (32a2ece) | **All 7 jobs passed** in 3 min 40 s: backend with system tests on Python 3.12 and 3.14, sandbox, frontend, browser end-to-end (Playwright: "17 passed (1.8m)"), dependency audit, and the container job (invalid setting refused, smoke test with real deployment records, logs without either token, health check, restart). |

Retention under load, from the notices the backend jobs wrote (Linux runners):

| Python | Seeded | Deleted, duration | Reads during retention | Reads afterwards |
|---|---|---|---|---|
| 3.12 | 1,152,010 in 10.9 s | 288,110 in 6.6 s | 27, median 127 ms, p95 1,084 ms, max 1,250 ms | 49, median 417 ms, max 718 ms |
| 3.14 | 1,152,010 in 8.2 s | 288,110 in 5.0 s | 38, median 110 ms, p95 622 ms, max 691 ms | 66, median 328 ms, max 438 ms |

As with Phase 6, job logs need a sign-in, so these results come from the run page and its
annotations. Whether the Linux-only test (an unwritable data directory) ran rather than being
skipped could not be read from there; it is expected to run, since the runner is not root.

## Defects found and fixed

1. **An invalid setting produced a Python traceback.** Starting with a short token, or
   `CLOUDOPS_SESSION_HOURS=abc`, printed uvicorn's traceback: not JSON, and for a type error naming
   Pydantic's field instead of the variable. Fixed by `cloudops serve` (D49).
2. **A busy database was reported as a server bug.** While another process held the write lock,
   writes waited 5 s and then failed with `500 internal_error`. They are now `503 database_busy`
   with `Retry-After` (D50).
3. **A damaged database file or unwritable data directory produced a traceback** from the start-up
   hook. It is now one critical line naming the file and the reason.
4. **Retention stopped health checks.** The worker loop awaited retention, so on a large database no
   check was started for as long as it ran (18 s at 1.15 million checks). A unit test that fails
   on the old code now covers it (D52).
5. **Retention scanned the whole index for every batch**, because a condition on time alone cannot
   use the (application, time) index. Deleting per application: 18 s to about 7 s.
6. **Finding each application's latest check read every check kept**: 110 ms at 1.15 million checks,
   growing with history, on every overview and application list. It is now one index lookup per
   application (under 1 ms).

Also found and fixed in the test tooling: `@axe-core/playwright` had installed a second
`playwright-core` (1.64.1, beside 1.64.0), which the new type check of the browser tests caught.
It is pinned to the test runner's version.

## Behaviour confirmed, not changed

- After a graceful stop, uvicorn re-raises the signal it caught, so the process ends "killed by
  SIGTERM" (Docker reports 143) rather than with status 0. On Windows, CTRL_BREAK ends it with 3.
  The shutdown itself is clean: `app.stopped` is logged and no errors.
- Readiness stays `ready` during a short write lock, because reads work. It turns `not_ready` only
  once the worker has failed to complete a tick for 30 s (D51).
- A check in flight when the server is killed is lost, not half-recorded; the application is
  checked again as soon as the server is back.

## Not established

- **System tests of a full disk** were not attempted: not practical to cause safely on this machine
  or a CI runner. A write failing for that reason would be `503 database_unavailable`, by the same
  handler as the other errors, but that has not been observed.
- **How the reads scale beyond the tested size.** Only 10 applications over 40 days were measured.

## Known limitations

- **Reads at this data size take hundreds of milliseconds.** The overview takes 220 to 330 ms on its
  own and about 0.5 s with two readers at once on 870,000 checks, mostly 24-hour availability
  computed in Python, and the health matrix about 600 ms. Acceptable for one operator; a
  precomputed summary would be the next step if it mattered.
- **The overview's first request after start-up can take about 2.7 s** while migrations and the
  first retention run share the database.
- **Browser tests run in Chromium only.**
- **The database is not shrunk after retention.** SQLite reuses the freed pages, so the file stops
  growing, but it does not get smaller without a `VACUUM`.
- Everything from Phase 6 still applies: no server, and images built only in CI.
