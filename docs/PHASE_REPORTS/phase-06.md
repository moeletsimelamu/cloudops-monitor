# Phase 6 — CI/CD and packaging

**Date:** 2026-11-07

## What was built

| Area | Implementation |
|---|---|
| One process | `backend/src/cloudops/spa.py`: with `CLOUDOPS_STATIC_DIR` set, the API serves the Angular build. Client routes get `index.html`; hashed files are cached for a year; missing assets and unknown API paths are 404s; nothing outside the build directory is reachable. Uvicorn's own messages now go through the application's JSON log handler. |
| Images | `Dockerfile` (Node build stage, then Python 3.14 slim runtime, non-root uid 10001, app files owned by root, data in `/data`, Docker health check on `/api/health/ready`) and `sandbox/Dockerfile` (non-root uid 10002). `.dockerignore` keeps out data, secrets, tests, docs and local environments. |
| Compose | `compose.yaml`: CloudOps Monitor and the sandbox, ports on `127.0.0.1` only, tokens required from `.env`, the sandbox allowed as a target and able to ship its logs. |
| Smoke test | `scripts/smoke.py`: ready, app shell, docs off, foreign host and anonymous requests refused, metadata address refused, register, healthy, failing with an incident, recovered with events, log shipped and masked, worker healthy, removed. Standard library only. |
| CI | `.github/workflows/ci.yml`: backend on Python 3.12 and 3.14, sandbox, frontend, dependency audit, and both images built, inspected and smoke-tested through Compose. |
| Delivery | `.github/workflows/deploy.yml`: CI, then build, smoke-test and publish to GitHub Container Registry, then a rollout job that runs only if a server is configured and otherwise says so. |
| Docs | `docs/DEPLOYMENT.md` (new); SECURITY §9, README, ARCHITECTURE and PLAN updated. |

## Verification

### On this machine

| Command | Result |
|---|---|
| `backend: python -m pytest` | **503 passed** (12 new: serving the frontend and the uvicorn log routing) |
| `backend: ruff check`, `ruff format --check` (backend and `scripts/`), `mypy` | Clean |
| `sandbox: pytest`, `ruff`, `mypy` | 17 passed; clean |
| `pip-audit --strict -r backend/constraints.txt` | No known vulnerabilities |
| `npm audit --omit=dev` | 0 vulnerabilities (with development packages included: 16 moderate, 0 high) |
| `scripts/smoke.py` against `uvicorn` serving the production build, with the sandbox | Passed, 10 steps, 18 s |
| Production build served by the API, in headless Chromium, 7 screens in light and dark | 0 console errors (so no Content Security Policy violations), 0 failed requests, 0 axe violations; styles and fonts loaded |

Docker is not installed here, so the images were built only in GitHub Actions.

### On GitHub

| Run | Result |
|---|---|
| CI #1, #2 (branch `phase-6-delivery`) | Container, frontend, sandbox and audit jobs passed. Backend tests failed on Linux on both Python versions: two test-harness bugs, below. |
| CI #3 | **All 6 jobs passed**, the image build and container smoke test included. |
| Deploy #1 (`main`, 32a8a41) | CI passed; the publish step failed at its first `docker push`. |
| Deploy #2 (`main`, 6b7d2d4) | **Success**: CI, build, smoke test, publish, and the rollout job ending with the notice "Not rolled out: No server is configured (missing: DEPLOY_HOST DEPLOY_USER DEPLOY_SSH_KEY DEPLOY_KNOWN_HOSTS DEPLOY_URL). The image was published; nothing was deployed to a server." |
| Registry | An anonymous request to `ghcr.io` listed the tags `sha-6b7d2d432775` and `latest`, so the image is public. |

GitHub shows job logs only to signed-in users. These results come from the public run pages, the
API and annotations, not from the logs. Test counts on GitHub were therefore not read; the counts
above are from local runs.

## Defects found and fixed

- **Test server race, hidden by Windows.** The test HTTP server printed its port before listening.
  Windows retries a refused connection for about 2 seconds, which covered the gap; Linux refuses at
  once, so the first probe test failed in CI. It now listens first.
- **Leak check assumed every socket was a network socket.** On Linux the event loop's wake-up
  socket is a Unix socket, whose peer name is a string. Only network sockets are counted now.
- **Failures could not be read.** Both of the above were diagnosed from annotations, which CI now
  writes for every failing test, because the logs need a sign-in.
- **Rotating the operator token does not end existing sessions.** Found while writing the
  operations guide. It is documented there and added to the Phase 8 review rather than changed
  here.

## Not established

- **Why Deploy #1 failed to push.** Its log needs a sign-in. That commit's tag never reached the
  registry, so the first push failed. The rerun, with the publish split into reported steps, pushed
  without error. The first publish of a new package may have been the difference, but that is a
  guess.

## Known limitations

- **No server.** Option B's rollout job is written but has never run, and the server-side
  `cloudops-deploy` command does not exist yet. Both belong to Phase 9 and need your decision.
- **Images never built locally.** Every image build and container test ran in CI only.
- **Browser end-to-end tests are not in CI yet.** CI checks the API journey (`smoke.py`), not the
  interface; the Playwright journey moves into the repository in Phase 7.
- **Development-only npm packages have 16 moderate advisories.** They are not shipped and not
  audited in CI.
- **`ubuntu-latest` is moving to Ubuntu 26** (a GitHub notice on every job). Nothing has broken, but
  a future runner image could change behaviour.
