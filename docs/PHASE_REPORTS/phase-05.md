# Phase 5 — Frontend and product design

**Date:** 2026-11-05

## What was built

An Angular 21 interface (`frontend/`) over the real API: standalone components, signals, no zone.js,
hand-written SVG charts, IBM Plex fonts served locally.

| Screen | What it does |
|---|---|
| Sign in | One token field. Says this is a single operator, not individual accounts. |
| Overview | Status counts; what needs attention, worst first, with how long and why; every application with availability, p95 and latest deployment; recent changes; recently closed incidents. |
| Applications | Sortable, filterable registry. Register and edit with inline validation, and the server's target policy shown beside the URL field. |
| Application detail | The troubleshooting workspace: status and timings, open incident, actions, the evidence timeline, then Checks, Events, Deployments, Logs and Configuration tabs. Selecting a period on the timeline narrows the tabs. |
| Health | Matrix of the worst check per period, with availability, coverage and p95 for the same window. |
| Deployments | Records read from GitHub Actions, with filters, and the state of each source. |
| Logs | Filters by application, severity, event, correlation ID and text. Rows expand. Older entries load on request. |
| Events | Timeline by day, filterable, linking to logs ±10 minutes around each event. |
| System | Readiness, worker, database, configuration warnings and effective non-secret configuration. |

Backend support added: `GET /api/health-matrix`, the worst signal per bucket, and an optional time
range on `GET /api/deployments`.

The interface has loading, refreshing, empty, error and pending states, a light and a dark theme,
local or UTC times, and the time range in the URL.

## Verification (commands actually run)

| Command | Result |
|---|---|
| `backend: python -m pytest` | **491 passed** |
| `backend: ruff check`, `ruff format --check`, `mypy` (strict) | Clean |
| `frontend: ng test --watch=false` | **76 passed** (4 files): registry rendering, health states, loading details, filtering deployments, searching and filtering logs, empty results, API failures, disabling actions while pending, chart gaps, the shell through the real router |
| `frontend: prettier --check` | Clean |
| `frontend: ng build` (production) | Exits cleanly; initial bundle 278.68 kB, 75.56 kB transferred |

The sandbox package did not change, so its tests were not rerun.

### In a browser, against the running API with real data

The API ran on 8300 and the sandbox on 8310, with `ng serve` on 4300. The data was real: checks of a
running process, a port with nothing listening on it, 2 requests a second of labelled synthetic
traffic with shipped logs, and 13 real GitHub Actions runs from `moeletsimelamu/documind`.

All of this used **headless Chromium driven by Playwright** from scripts outside the repository. The
desktop app's browser pane could not take screenshots while its window was hidden. Phase 7 moves
these checks into the repository.

- **Every screen, every size.** 14 routes at 1440, 1024 and 390 px in light and dark (84 captures).
  Console errors and warnings: 0. Failed API requests: 0. Sideways page scrolling: 0. axe violations:
  0. Screenshots were also read by eye, which is how most defects below were found.
- **Keyboard only.** Skip link, sign-in, overview, into the unhealthy application, the chart, tabs with
  arrow keys, Sign out. A 2 px focus ring was present at each step checked.
- **Actions through the real interface, 22 of 22 passed:**
  - wrong token refused, right token accepted, sign-out ends the session;
  - a cloud metadata address refused with the server's reason, an allowed target registered;
  - Check now recorded a new check;
  - sandbox switched to `errors`: the interface showed Degraded and the open incident; switched back:
    Healthy, incident closed;
  - selecting a chart period narrowed the tabs;
  - pause and resume;
  - a new ingest key is shown, and not shown again after reload;
  - log text filter returned only matches, a row expands, no matches shows an empty state;
  - deployment outcome filter went from 13 to the 2 real failures;
  - time zone and theme survive a reload;
  - removal: Cancel keeps the application, confirming removes it.
- **Losing the API.** Imitated by blocking `/api` in the browser, not by stopping the server. The page
  kept its data, said it could not refresh, and recovered on Retry.

## Defects found and fixed

Found by running the app and looking at it. The unit tests passed through most of these.

- **Every page crashed.** The shell read a child route's snapshot while the router was still activating
  it, leaving an empty heading and empty buttons. Unit tests had rendered the shell directly. They now
  go through the real router.
- **Real deployments hidden.** The page said there were no deployments while 13 were stored: the newest
  was 9 days old and the longest range is 7 days. Lists now show the newest at any age.
- **`ng build` never exited.** See decision D36. The cause within the builder was not established.
- **Status colours too close** for colour-blind readers. Replaced after validation (DESIGN §3).
- **No way to sign out between 720 and 1199 px**, and rail links there had no accessible name (axe,
  serious).
- **Unusable check list on a phone**: over 9,000 px tall, a word per line. Each check now takes two
  short lines.
- **Newest chart data off-screen on a phone.** The chart now fits.
- **A made-up "1 ms" scale** for an application that never responded. It now says no responses were
  received.
- **A blank gap above the heading** on short pages at phone width (grid rows sharing spare height).
- **Times and durations wrapping** in table cells; a heading-order finding on Events; cross-field
  validation messages not shown on the form; mono font on numeric column headers; a missing full stop
  in the target policy text; a chart label collision; a stray missing rule in the readiness table.

## Known limitations

- **Not served by the API yet.** It runs from `ng serve`; the built app behind the API and its
  Content Security Policy are first exercised in Phase 6.
- **One browser.** Only Chromium. No Firefox, Safari or real phone.
- **No screen reader run.** axe and a keyboard pass are not the same thing. Planned for Phase 8.
- **No repeatable browser test yet.** The checks above depend on a hand-prepared database and scripts
  outside the repository, so CI cannot run them until Phase 7.
- **Correlation note not seen on screen with real data.** It needs a deployment shortly before an
  incident. It is covered by a frontend test with a fixture and by backend tests with fake GitHub runs.
- **Polling, not push.** Each open page refreshes every 15 seconds, so changes can take that long to
  appear.
- **Differences from the Phase 1 design.** No custom time range, virtualised log list, comfortable
  density, breadcrumbs, deployments-per-day chart, linked incident pairs on Events, or filter sheet on
  phones. DESIGN.md and the decision log record these.
