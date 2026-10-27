# Phase 1 — Discovery and architecture

**Date:** 2026-10-27

## What was done

- Inspected the environment: Python 3.14.3, Node 22.14.0, npm 10.9.2, Angular CLI 21.2.13 and Git
  2.54 are installed. Docker, GitHub CLI, WSL and the cloud CLIs are not.
- Confirmed that OpsHub and DocuMind are separate repositories with clean histories. Neither was
  modified. A new repository was created at `Desktop/CloudOpsMonitor`, with the local git identity
  set before the first commit.
- Checked package versions against PyPI and npm (alembic 1.20.1, httpx2 2.16.0, @fontsource IBM
  Plex 5.3.0, Angular 21.2.25). The rest of the version plan reuses the set already working
  together in DocuMind.
- Wrote [PLAN.md](../PLAN.md), [ARCHITECTURE.md](../ARCHITECTURE.md), [DESIGN.md](../DESIGN.md),
  [SECURITY.md](../SECURITY.md) and a draft README. Added `.gitignore`, `.gitattributes` and
  `.editorconfig`.

## Key decisions

See the decision log in PLAN.md (D1–D14). The most significant:

- Modular monolith with an in-process worker; SQLite with Alembic.
- Target allowlist by default, with pinned connections and no redirects, as SSRF protection.
- Check classification into four signals, and a state machine with hysteresis. Staleness is
  computed when data is read.
- Deployment records pulled from GitHub Actions, read only. No deployment trigger.
- A controllable sandbox target instead of simulated data.
- Baseline deployment is an image published to GHCR. A VM deployment happens only with approval.

## Verified

Nothing runnable exists yet. Only the environment inspection and version look-ups above were
performed.

## Open items

- B1: the GitHub repository needs to be created by the developer before Phase 6.
- B2: without Docker locally, image verification will happen in CI only.
- B3: the hosting decision is deferred to Phase 9.
