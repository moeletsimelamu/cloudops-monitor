# CloudOps Monitor — Design

> **Status:** Phase 1 design intent. Screens are built in Phase 5. This document is then updated to
> match what was built and verified in the browser.

## 1. Who it is for and what they are doing

The user is an engineer who has just been told that "something is wrong", or who suspects their own
deploy broke something. They want, in this order:

1. **What is broken right now?**
2. **Since when, and how badly?**
3. **What changed around that time?** (deployments, configuration)
4. **What does the evidence say?** (checks, logs, events)
5. **What can I do next?** (open the CI run, pause monitoring, check again)

Every screen is judged by how quickly it answers its part of that sequence.

## 2. Principles

- **Current state first, history second.** Each view separates "now" (top, compact, always
  visible) from "what happened" (below, filterable).
- **Every number says where it comes from.** It shows a time window, a unit and a source. Examples:
  "24 h · 1,382 checks", "p95 over 15 min buckets", "from ingested request logs".
- **Freshness is visible.** Every status shows its age ("checked 34 s ago"). Stale data looks
  different from fresh data.
- **Density without clutter.** Compact tables, aligned numbers and hairline separators instead of
  boxes in boxes.
- **No decoration.** No hero sections, gradients, glassmorphism, glow, fake terminals or charts
  without a question behind them.
- **Empty is honest.** No data shows as "no data", with the reason and what to do. It is never
  zero, a flat line or a placeholder chart.

## 3. Visual identity — "instrument panel"

The interface takes cues from printed engineering instruments and timetables: neutral paper and
graphite surfaces, ink-coloured text, one accent colour for interaction, and status colours kept
only for status.

### Colour

| Token | Light | Dark | Use |
|---|---|---|---|
| `--surface-0` | `#f6f6f3` | `#121416` | page |
| `--surface-1` | `#ffffff` | `#1a1d20` | panels, tables |
| `--rule` | `#dcdcd6` | `#2c3035` | hairlines, separators |
| `--ink` | `#16191c` | `#e7e8e6` | primary text |
| `--ink-muted` | `#5b6168` | `#9aa1a8` | secondary text, labels |
| `--accent` | `#1d5fbf` | `#6ea3f0` | links, focus ring, primary buttons, selection |
| `--ok` | `#1d7a46` | `#4cc283` | healthy |
| `--warn` | `#9a5b00` | `#e2a43a` | degraded |
| `--fail` | `#b42318` | `#f0766a` | unavailable, failed deploy |
| `--neutral` | `#6b7178` | `#8b9299` | unknown, stale, paused, cancelled |

Every text and icon pair is checked for WCAG AA contrast (4.5:1 for text, 3:1 for glyphs and
chart marks) in both themes during Phase 5. Values are adjusted if a pair fails. The theme follows
`prefers-color-scheme` and has a manual override.

### Status is never colour alone

Each state has a **shape**, a **colour** and a **word**:

| State | Glyph | Label |
|---|---|---|
| Healthy | ● filled circle | Healthy |
| Degraded | ▲ triangle | Degraded |
| Unavailable | ■ square | Unavailable |
| Stale | ◌ dashed circle | Stale · 14 min |
| Pending / unknown | ○ hollow circle | Pending |
| Paused | ‖ bars | Paused |

Charts use the same shapes as markers, and failure spans use hatching, so they read in greyscale
and for colour-blind users.

### Typography

- **IBM Plex Sans** for the interface: base 14 px; 13 px in dense tables; headings 20/16/14 px with
  weight rather than size doing most of the work.
- **IBM Plex Mono** for machine data: timestamps, durations, status codes, commit SHAs, URLs,
  correlation IDs. Every numeric column uses tabular figures and is right-aligned.
- Both fonts are self-hosted through `@fontsource`. There are no runtime requests to font CDNs.

### Shape and space

4 px spacing grid. Corner radius at most 3 px. 1 px rules instead of shadows. Table rows are 32 px
(compact) or 40 px (comfortable, a per-user setting). Panels are separated by rules and headings,
not by floating cards.

## 4. Layout and navigation

A persistent shell:

- **Left rail** (collapses to icons below 1200 px and to a top bar with a menu below 720 px):
  Overview · Applications · Health · Deployments · Logs · Events · System. Each entry shows a
  status badge where relevant, for example a count of open incidents on Overview.
- **Top bar**: page title and breadcrumbs, the **global time range** (1 h / 6 h / 24 h / 7 d /
  custom, stored in the URL query so links are shareable), a UTC/local time toggle, and a "data as
  of 12:04:31" freshness indicator with auto-refresh status.
- The content area changes layout per task. It is not one template repeated.

## 5. Screens

| Screen | Primary question | Layout |
|---|---|---|
| **Overview** | What needs attention? | 1) A *Needs attention* list: open incidents and stale applications, ordered by severity and then age, each with state, duration and the deployment that preceded it, if any. 2) A *Fleet* table: every application with displayed status, last check age, 24 h availability, p95 latency and latest deployment. 3) *Recent changes*: a single chronological list merging state transitions and deployments. A one-line count strip (12 apps · 9 healthy · 2 degraded · 1 stale) replaces big summary cards. |
| **Applications** (registry) | What is monitored and how? | A sortable, filterable table (environment, status, monitoring on/off). Register and edit open as a full page with inline validation and the target policy explained next to the URL field. |
| **Application detail** | Why is this one unhealthy? | The **troubleshooting workspace**. A header with status, since when, last success and failure, and actions (Check now, Pause, Edit, Open repository). Then the **evidence timeline** (§6). Then tabs: *Checks*, *Events*, *Deployments*, *Logs* and *Configuration*, all scoped to this application and the selected time range. |
| **Health** (history) | How did everything behave over time? | A matrix: rows are applications, columns are time buckets, and each cell shows the worst signal in that bucket (shape + colour; hatched when there is no data). Availability and coverage appear per row. Clicking a cell opens the application detail at that time. |
| **Deployments** | What shipped, when, and did it work? | A table filterable by application, outcome and branch: commit SHA, title, actor, duration, outcome and link to the GitHub run. A strip chart of deployments per day for the range. Per application, it shows when the last sync ran. |
| **Logs** | What did the application say? | A filter bar (application, minimum severity, event, correlation ID, text) above a virtualised list of compact single-line entries. A row expands to show attributes. Clicking a correlation ID filters by it. A "±10 min" action is available from any other screen. |
| **Events** | What changed in the system? | A vertical timeline grouped by day. Event types are filterable. Incident open and close pairs are linked with their duration. |
| **System** | Is the monitor itself OK? | Liveness and readiness results, worker heartbeat age and loop lag, last retention run, GitHub sync status and rate limit, database size, the effective non-secret configuration and configuration warnings, plus the target policy and allowlist. |
| **Sign in** | — | A single token field. Says plainly that this is a single-operator token. |

## 6. Signature component: the evidence timeline

One horizontal time axis on the application detail page that layers, from top to bottom:

1. **State band**: incident spans hatched in the incident's worst state, with healthy periods
   plain.
2. **Checks**: response time as a line, with failed checks as shape markers on the axis and gaps
   where no observation exists (not interpolated).
3. **Deployments**: vertical markers with the run number. Hovering or focusing a marker shows the
   commit, outcome and duration.
4. **Request errors**: 5xx counts per bucket from ingested request logs, shown only when such logs
   exist.

Selecting a point or range filters the tabs below to that time. This is how the product scenario
works: degradation, a deployment just before it, and error logs, all on one screen.

Charts are hand-written SVG components that share axis and scale helpers. Each chart has a visible
title, units, a time range, an `aria-label` summary (for example "Response time, 24 h, p95 212 ms,
3 failed checks"), and a table alternative ("View as table").

## 7. States

| State | Treatment |
|---|---|
| Loading (first load) | Skeleton rows with the table's real layout. No spinners over empty pages. |
| Refreshing | Existing data stays visible. A subtle "updating…" next to the freshness indicator. |
| Empty, never configured | Says what is missing and links to the fix, for example "No deployment source configured for this application — add a GitHub repository and workflow in Configuration". |
| Empty, no results | Says which filters produced nothing and offers to clear them. |
| Error | What failed, what the user can do (retry, check System, sign in again) and the request ID. Partial failures, such as logs failing while checks load, are shown per panel. |
| Pending action | Buttons disable and show progress. Double submits are impossible. The result is announced through an `aria-live` region. |

## 8. Accessibility

- Every interactive element is reachable and operable by keyboard, with a visible 2 px `--accent`
  focus ring and offset.
- A skip link to the main content, landmark regions and one `h1` per page.
- Tables use real `<table>` markup with `<th scope>` and sortable headers announced through
  `aria-sort`.
- Form fields have visible labels. Errors are linked with `aria-describedby` and explain how to fix
  them.
- Live regions announce check results and status changes on the detail page, rate-limited so they
  don't flood screen readers.
- `prefers-reduced-motion` disables the few transitions.
- Verified with axe in Playwright tests and by manual keyboard passes in the browser (Phase 8).

## 9. Responsive behaviour

- **≥ 1200 px**: full rail, the detail timeline at full width, tabs below.
- **720–1199 px**: icon rail. Tables drop low-priority columns (actor, branch) into the expandable
  row.
- **< 720 px**: top bar with a menu. Fleet and deployment tables become stacked rows with status,
  name, age and one key metric. The timeline keeps its time axis and scrolls horizontally inside its
  own panel, never the page. Filter bars collapse into a "Filters (3)" sheet.
