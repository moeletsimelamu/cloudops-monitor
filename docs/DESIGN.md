# CloudOps Monitor — Design

> **Status:** Built in Phase 5. This document describes what exists and was checked in a browser;
> where the build differs from the Phase 1 intent, it says so and the decision log in
> [PLAN.md](PLAN.md) gives the reason.

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
| `--ink-muted` | `#565c63` | `#a3a9b0` | secondary text, labels |
| `--accent` | `#1d5fbf` | `#6ea3f0` | links, focus ring, primary buttons, selection |
| `--ok` | `#0f6b3a` | `#7fe0ad` | healthy |
| `--warn` | `#bd7a00` | `#f5c45a` | degraded |
| `--fail` | `#b5261b` | `#e8604f` | unavailable, failed deploy |
| `--neutral` | `#6b7178` | `#8b9299` | unknown, stale, paused, cancelled |

The Phase 1 status colours were too close together for colour-blind readers, so they were replaced
after running them through a palette validator. The smallest difference between any two statuses
under simulated colour blindness (OKLab ΔE) is 7.9 in light and 9.6 in dark. The light value is below
the target of 8, which is acceptable only because status is never colour alone (below). Text wears
the ink tokens, never a status colour. axe reports no contrast violations on any screen, in either
theme. The theme follows `prefers-color-scheme` and has a manual override.

### Status is never colour alone

Each state has a **shape**, a **colour** and a **word**:

| State | Glyph | Label |
|---|---|---|
| Healthy | ● filled circle | Healthy |
| Degraded | ▲ triangle | Degraded |
| Unavailable | ■ square | Unavailable |
| Stale | ◌ dashed circle | Stale |
| Pending / unknown | ○ hollow circle | Pending |
| Paused | ‖ bars | Paused |

Charts encode the same thing without colour: incident spans are hatched, and check results and the
health matrix use height (short ok, medium warning, tall failed), so they read in greyscale.

### Typography

- **IBM Plex Sans** for the interface: base 14 px; 13 px in dense tables; headings 20/16/14 px with
  weight rather than size doing most of the work.
- **IBM Plex Mono** for machine data: timestamps, durations, status codes, commit SHAs, URLs,
  correlation IDs. Every numeric column uses tabular figures and is right-aligned.
- Both fonts are self-hosted through `@fontsource`. There are no runtime requests to font CDNs.

### Shape and space

Corner radius at most 3 px. 1 px rules instead of shadows. One compact table density; the planned
"comfortable" setting was not built, because nothing needed it. Panels are bordered sections with a
heading row, not floating cards.

## 4. Layout and navigation

A persistent shell:

- **Left rail** (collapses to icons below 1200 px and to a top bar with a menu below 720 px):
  Overview · Applications · Health · Deployments · Logs · Events · System. Each entry shows a
  status badge where relevant, for example a count of open incidents on Overview.
- **Top bar**: page title, the **global time range** (1 h / 6 h / 24 h / 7 d, stored in the URL query
  so links are shareable, and hidden on pages it does not apply to), a UTC/local time toggle and a
  theme control. There are no breadcrumbs and no custom range: the log links from other screens
  ("±10 min") cover the case a custom range was for.
- Data refreshes every 15 seconds while a page is open. The Overview shows "Data as of 12:04:31".
- The content area changes layout per task. It is not one template repeated.

## 5. Screens

| Screen | Primary question | Layout |
|---|---|---|
| **Overview** | What needs attention? | 1) A *Needs attention* list: open incidents and stale applications, ordered by severity and then age, each with state, duration and the deployment that preceded it, if any. 2) A *Fleet* table: every application with displayed status, last check age, 24 h availability, p95 latency and latest deployment. 3) *Recent changes*: a single chronological list merging state transitions and deployments. A one-line count strip (12 apps · 9 healthy · 2 degraded · 1 stale) replaces big summary cards. |
| **Applications** (registry) | What is monitored and how? | A sortable table filterable by text, environment and status. Register and edit open as a full page with inline validation and the target policy explained next to the URL field. |
| **Application detail** | Why is this one unhealthy? | The **troubleshooting workspace**. A header with status, since when, last success and failure, and actions (Check now, Pause, Edit), with the open incident and a link to the GitHub run of any deployment that preceded it. Then the **evidence timeline** (§6). Then tabs: *Checks*, *Events*, *Deployments*, *Logs* and *Configuration*, all scoped to this application and the selected time range. |
| **Health** (history) | How did everything behave over time? | A matrix: rows are applications, columns are time buckets, and each cell shows the worst signal in that bucket by height and colour, left empty when no check ran. Availability, coverage and p95 appear per row. Clicking a row's cells opens that application, keeping the time range. |
| **Deployments** | What shipped, when, and did it work? | A table filterable by application, outcome and branch: commit SHA, title, actor, duration, outcome and link to the GitHub run. Newest first at any age, not limited by the time range, because deployments are rare. Below it, each source's last sync and any problem. The planned per-day strip chart was left out: the table already answers the question. |
| **Logs** | What did the application say? | A filter bar (application, minimum severity, event, correlation ID, text) above a list of compact single-line entries, 100 at a time with "Load older entries" (not virtualised). A row expands to show attributes. Clicking a correlation ID filters by it. A "±10 min" action is available from any other screen. |
| **Events** | What changed in the system? | A vertical timeline grouped by day. Filterable by application, kind and minimum severity. Each entry links to its application and to its logs ±10 minutes. Recovery events state how long the problem lasted. |
| **System** | Is the monitor itself OK? | Liveness and readiness results, worker heartbeat age and loop lag, last clean-up, database size and counts, the effective non-secret configuration and configuration warnings, plus the target policy and allowlist. GitHub sync status and rate limit are on Deployments. |
| **Sign in** | — | A single token field. Says plainly that this is a single-operator token. |

## 6. Signature component: the evidence timeline

One horizontal time axis on the application detail page that layers, from top to bottom:

1. **Incidents**: spans hatched in the incident's worst state.
2. **Deployments**: markers with the outcome's shape. Hovering shows the commit, outcome and duration.
3. **Response time**: p95 as a line, with gaps where no observation exists (never interpolated). If no
   response was received at all, it says so and draws no scale, rather than an invented one.
4. **Checks**: the worst result per period, by height and colour.
5. **Request errors**: 5xx counts per bucket from ingested request logs, shown only when such logs
   exist.

Selecting a period filters the tabs below to that time. This is how the product scenario
works: degradation, a deployment just before it, and error logs, all on one screen.

Charts are hand-written SVG components that share axis and scale helpers. Each chart has a visible
title, units, a time range, an `aria-label` summary, a keyboard readout (arrow keys move between
periods) and a table alternative ("View as table").

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
- Checked in Phase 5 with axe in headless Chromium on 14 routes at 3 widths and in both themes (no
  violations), and a scripted keyboard-only journey. In Phase 8, axe at every impact level (not only
  serious and critical) found nothing on any screen at 320 and 1280 px in both themes, with the sign-in
  page and an open dialog included; nothing scrolls sideways at 320 px, the width WCAG uses for 400 %
  zoom; and keyboard use (skip link, navigation, dialog focus) is a browser test in CI. It has still
  not been tried with a real screen reader.

## 9. Responsive behaviour

- **≥ 1200 px**: full rail, the detail timeline at full width, tabs below.
- **720–1199 px**: icon rail, with each link's name kept for screen readers and shown as a tooltip.
- **< 720 px**: top bar with a menu. Tables drop low-priority columns; the check list gives each
  check's detail a line of its own. The timeline shrinks to fit, because the newest data is at the
  right and must not start off-screen, and shows fewer time labels. Filters wrap rather than
  collapsing into a sheet. A table that still does not fit scrolls inside its own panel, never the
  page.
