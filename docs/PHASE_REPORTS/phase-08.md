# Phase 8 — Security and quality review

**Date:** 2026-11-08

The brief's Phase 8 asks for a review of SSRF protection, authentication and authorisation
assumptions, secrets management, input validation, log redaction, container configuration,
dependency risks, error handling, accessibility and responsive behaviour, and a run of the whole
suite with the actual results. Phase 6 added one item: a way to end every session when the operator
token is rotated.

The review itself, with how each finding was reproduced, is [REVIEW.md](../REVIEW.md). This report
summarises it and records the verification.

## Findings and fixes

Each finding was reproduced against the unchanged code before it was fixed.

| # | Finding | Reproduced on the old code | Fix |
|---|---|---|---|
| F1 | Sessions survived a token rotation; no way to end them all | A cookie from before the rotation: `200` | Sessions stored as an HMAC keyed with the token; *Sign out everywhere* (System page, `DELETE /api/auth/sessions`) |
| F2 | Wrong bearer tokens were never limited | 20 wrong bearer tokens, then the right one: `200` | They share the sign-in limit (5 a minute per client) |
| F3 | Bodies of any size were read before authentication | An unauthenticated 20 MiB sign-in body read in full: `422` | Every body limited as it arrives: 64 KiB, 256 KiB for log batches |
| F4 | NAT64, IPv4-compatible, 6to4, Teredo and site-local IPv6 addresses passed the SSRF check | 8 of the 9 new address cases fail | Checked as themselves and as the IPv4 address inside |
| F5 | Forwarded headers believed from `127.0.0.1`, not from a real proxy | Read from the code and uvicorn's defaults; the new system tests show both sides | `CLOUDOPS_TRUSTED_PROXIES`, empty by default; no `server` header |
| F6 | Nested log fields and private keys were not redacted | A GitHub token in a list, and a PEM key, written as they were | Redaction at any depth; PEM, Slack, Stripe and Google formats |
| F7 | Writable root file system and default capabilities; actions on moving tags | Read from `compose.yaml` and the workflows | Read-only root, no capabilities, no privilege gain, checked in CI; actions pinned to SHAs |

## What was built

| Area | Change |
|---|---|
| Sessions | HMAC-keyed session storage; `DELETE /api/auth/sessions`; a *Sessions* section on the System page with a confirmation dialog |
| Limits | Bearer failures counted; a body limit on every path |
| SSRF | IPv6 transition addresses unwrapped; site-local blocked in public mode |
| Proxies | `CLOUDOPS_TRUSTED_PROXIES` (validated at start-up, shown on the System page); uvicorn's own forwarded-header handling and `server` header off |
| Redaction | Recursive for structured fields; more credential formats |
| Containers | `read_only`, `tmpfs: /tmp`, `cap_drop: [ALL]`, `no-new-privileges` for both services; a CI step that checks them on the running containers |
| Workflows | Every action pinned to a commit SHA |
| Tests | `tests/system/test_security.py` (4 real-process tests); unit tests for each finding; browser tests `keyboard.e2e.ts` (2) and `sessions.e2e.ts` (1); a component test of the new button |
| Documents | REVIEW.md (new); SECURITY.md §3, §4, §5, §6, §7, §8, §9 and §10; DEPLOYMENT.md (proxies, rotation, hardened `docker run`); ARCHITECTURE.md; DESIGN.md; README; `.env.example` |

## Verification

### On this machine (Windows 11, Python 3.14, Node 22.14)

| Command | Result |
|---|---|
| `backend: python -m pytest` (fast and system tests) | **565 passed, 1 skipped** in 4 min 16 s. The skipped test needs POSIX permissions; it runs in CI. Of these, 21 are system tests (4 new). |
| `ruff check`, `ruff format --check`, `mypy`, `mypy --platform linux` | Clean |
| `sandbox: python -m pytest` | 17 passed |
| `frontend: npm test` | **78 passed** (2 new) |
| `frontend: npm run e2e` (after `npm run build`) | **20 passed** in 2.4 min (3 new) |
| `npx tsc -p e2e/tsconfig.json`, `npm run format:check` | Clean |
| `pip-audit --strict` on `backend/constraints.txt` | No known vulnerabilities |
| `npm audit --omit=dev` / `npm audit` | 0 / 16 moderate, all in development-only tools |
| A one-off axe audit at every impact level (13 views, 320 and 1280 px, light and dark) | No findings; no horizontal scrolling at 320 px |

Also found in the test tooling: the system test that kills the server failed about one run in five
on Windows with `disk I/O error`, opening the database straight after the kill. Ten deliberate kills
showed why: Windows reports the process gone before it releases the database files, the first open
fails, and 60 to 80 ms later it opens with an integrity check of `ok` every time. The harness now
waits for the files to be released after a kill on Windows; six repeated runs and the full suite
passed afterwards. The database was never damaged, and a real restart is never that fast.

### On GitHub

| Run | Result |
|---|---|
| CI #9 (branch `phase-8-review`, 1081d19) | **All 7 jobs passed** in 3 min 35 s: backend with system tests on Python 3.12 and 3.14, sandbox, frontend, browser end-to-end (Playwright: "20 passed (1.6m)"), dependency audit, and the container job. Every action ran from its pinned commit. |

In the container job, every step succeeded, among them the new one: both running containers have
a read-only root file system, `no-new-privileges` and an empty capability bounding set, and cannot
write outside their volume. The smoke test, Docker's health check and the restart then all passed
under those settings. As in earlier phases, job logs need a sign-in, so these results come from the
run page, its step list and its annotations.

Retention under load, from the backend jobs' notices: on Python 3.14, 288,110 checks deleted in
3.9 s with reads during retention at a median of 190 ms (max 538 ms); on Python 3.12, 6.5 s, median
431 ms (max 1,086 ms). These are in line with Phase 7.

## Not established

- **A screen reader pass.** axe at every impact level and keyboard tests pass, but nothing was heard
  through NVDA or VoiceOver; none is available to this session.
- **The container hardening on a real Docker host here.** Docker is not installed locally; the
  read-only file system, dropped capabilities and the smoke test under them are verified only in CI.
- **IPv6 over a real network.** The SSRF tests give the probe IPv6 answers through its resolver;
  neither this machine nor the CI runners have IPv6 configured.

## Known limitations

SECURITY.md §10 now lists them, with what each means and what would address it. In short: one
shared operator token without user accounts, an in-memory sign-in limit, pattern-based redaction,
an allowlist that trusts DNS for listed names, Python packages pinned by version but not by hash,
base images pinned by tag but not digest, no automated dependency updates, development-only npm
advisories, no screen reader pass, and no HTTPS without a proxy. Everything from Phase 7 still
applies: no server, and images built only in CI.
