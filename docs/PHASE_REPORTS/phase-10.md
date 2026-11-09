# Phase 10 — Portfolio readiness, and the final report

**Date:** 2026-11-09

Phase 10 asked for a final README, real screenshots using the sandbox and fictional application
names only, a check of every claim, and a final report. This file is both the phase report and the
report on the project as a whole.

## Screenshots from a real run

The eight screenshots in `docs/screenshots/` come from one run on this machine. A script started
CloudOps Monitor's production build with `cloudops serve` and an empty database. It registered
four sandbox processes under fictional names (Orders API, Payments service, Catalog search,
Notifications worker), each checked every 15 s and shipping a log entry for every request it
answered, from 0.5 to 2 of its own API calls a second. Over 52 minutes it changed which response
each sandbox gave, on a fixed schedule written to a timeline file with the time of each change.
The README's "About the screenshots" section reproduces that timeline.

The deployment on the Orders API timeline is real. Orders API was linked to this repository's own
Deploy workflow; the documentation fixes of this phase (ac9466b) were pushed to `main`, Deploy
run #8 ran on GitHub and finished at 12:46:54 (UTC+2), CloudOps synced it, and only then was the
Orders API sandbox switched to failing. CloudOps reported the incident as opening "1 min after
deployment #8 (ac9466b) finished (succeeded)" and that this "reports timing only, not a cause".

Not staged: in the first minute, three sandboxes took over a second to answer while all five
processes started at once, so short "Slow response" incidents appear in the overview's "Recently
failed" list. They were left in and are explained in the README.

The capture itself reported no script or console errors on any of the nine pages captured. Each
image was looked at before it was used; the application page was taken again at window height so
that the README's first image is not 2,500 pixels tall.

## The claim check

Every factual statement in the README was checked against the code or a run, and the other
documents were read for statements that later phases had made untrue. Corrected:

| Where | Was | Now |
|---|---|---|
| ARCHITECTURE §7.1 | `/api/system/status` included "GitHub sync status and rate-limit remaining" | It does not; that is `/api/deployments/sources`, which the row now names |
| ARCHITECTURE §7.1, §7.2 | Build-time notes ("Implemented in Phase 2", "added with the worker in Phase 3") and the status route listed a second time among the application routes | Removed |
| ARCHITECTURE §1, §2 | A `python -m cloudops.worker` command and a "repositories" layer that never existed; the request-ID middleware credited to `logging_setup.py`; `middleware.py`, `auth.py`, `errors.py`, `cli.py`, `spa.py` missing | Matches the modules as built |
| ARCHITECTURE §3.7 | Windows `1h`, `24h` and `7d` | Also `6h`, which the API and the interface offer |
| ARCHITECTURE §5.1 | The redaction list from Phase 4 | Includes the formats added in Phase 8 |
| ARCHITECTURE §8 | A `sessions` table; `deployments` without `workflow_name` and `queued_at` | `operator_sessions`; the columns as in the models |
| ARCHITECTURE §10, DESIGN §6 | "Hand-written SVG charts", each with arrow keys and a table view | The timeline is SVG and has both; the health matrix is HTML and CSS with a text summary per row, and the documents now say so |
| PLAN §3 | The Phase 1 folder plan (`unit/` and `integration/` test folders, `logging.py`, a scheduler module) | The structure as built |
| PLAN §4 | Image `python:3.14-slim`; a Phase 1 "if not" note about httpx2; probe tests on `MockTransport`; two npm overrides missing | The real image tag; how the probe is built (D20); probe tests on real servers (D34); every override, with the `undici` one described by what it does, because why it was added was never recorded |
| PLAN §5 | The Phase 8 tests and the release verification missing | Listed |

The README itself was rewritten for someone seeing the project for the first time. Its claims were
checked as follows:

- **Versions:** against `pyproject.toml` and `package.json`.
- **Behaviour and accessibility:** against the code or the tests that cover them. Where a claim went
  further than the tests, it was narrowed: "every screen works with a keyboard" became "the main
  journey".
- **Numbers:** from the runs below.
- **Links:** every relative link and image path resolves (checked by script).

## Verification

### On this machine (Windows 11, Python 3.14, Node 22.14)

| Command | Result |
|---|---|
| `backend: python -m pytest` (unit, API and system tests) | **565 passed, 1 skipped** in 5 min 7 s (545 unit and API, 21 system; the skipped one needs POSIX permissions and runs in CI) |
| `ruff check`, `ruff format --check`, `mypy`, `mypy --platform linux` | Clean (90 source files) |
| `sandbox: python -m pytest` | 17 passed |
| `frontend: npm test`, `npm run format:check` | 78 passed; formatting clean |
| `frontend: npm run build; npm run e2e` | **20 passed** in 2.4 min |

681 tests in all, one of them skipped here. Nothing in the code changed in this phase, so these
match Phase 8's counts; they were run again so that the README's numbers come from this phase.

### On GitHub

| Run | Result |
|---|---|
| Deploy #8 (ac9466b, the documentation fixes) | **Passed** in 5 min 38 s: all of CI, publish, the published image pulled signed out and run, "Not rolled out". |

The run for the commit that adds this report and the screenshots is added below once it has finished.

## The project as a whole

CloudOps Monitor was built in ten phases between 2026-10-27 and 2026-11-09, each approved before
the next began. Each has a report in this folder with the commands that were run and their real
output.

| Phase | Result |
|---|---|
| 1. Discovery and architecture | Scope, threat model, design, and a plan with costs; approved 2026-10-27 |
| 2. Backend foundation | Settings, auth, errors, logging, database and migrations; 95 tests |
| 3. Registry and monitoring | The SSRF policy with pinned connections, the probe, classification, the state machine, incidents and the worker; a live run against the sandbox |
| 4. Events, logs, deployments | Events, log ingestion and search, GitHub Actions deployment records, correlation; a real GitHub sync |
| 5. Frontend | Every screen in Angular, in both themes, checked in a browser against real data |
| 6. CI/CD and packaging | The image, Compose, CI and Deploy workflows; the image published to GHCR |
| 7. Integration hardening | System tests of the real process and Playwright tests in CI; six defects fixed |
| 8. Security and quality review | Seven findings reproduced and fixed; the known limitations completed |
| 9. Deployment | Option A only, by the owner's decision (no budget for a server); every release pulls and runs the published image; the README followed from a fresh clone |
| 10. Portfolio readiness | Real screenshots, the README, the claim check, this report |

**What exists:**
- **The product:** a FastAPI service with an in-process monitoring worker and an Angular
  interface, as one container image.
- **The sandbox:** a controllable test target.
- **Tests:** 681 automated tests (counted above) and a smoke test.
- **Delivery:** CI and delivery workflows on GitHub Actions.
- **Records:** 63 decisions with their reasons in PLAN.md, and these ten reports.

**Defects found by the project's own checks,** rather than reported by anyone else:
- **Phase 7:** six, found by running the real server.
- **Phase 8:** seven, found by the security review.
- **Other phases:** several found while testing, each recorded in its phase report. Two examples:
  the Phase 3 live run found that `localhost` checks waited 2 s for IPv6, and reading screenshots
  found most of the Phase 5 interface defects.

**Not done, and why:**
- **Running on a server:** it was designed, and the workflow supports it, but no server was paid
  for (D62).
- **Docker on this machine:** it is not installed, so the container is verified only on GitHub's
  runners.
- **A screen reader:** none is available here.
- **Browsers:** Chromium only.
- **A real IPv6 network:** neither this machine nor the CI runners have one, so IPv6 is tested only
  through the probe's resolver.

**Limitations that are part of the design:**
- one operator token;
- one process and one SQLite file;
- checks from one place;
- no notifications;
- pattern-based redaction.

The README and [SECURITY.md §10](../SECURITY.md#10-known-limitations) state them.
