# Phase 9 — Deployment and final verification

**Dates:** 2026-11-08 to 2026-11-09

The plan for this phase was Option A (the container image published to GitHub Container Registry)
in every case, and Option B (one small server) only after a price check and the owner's approval.
The price check was done; the owner decided against a server because there is no budget for one
(D62). Phase 9 therefore finishes with Option A, verified as a user would meet it.

## What was done

| Area | Change |
|---|---|
| Published image | A new Deploy job, *Pull and run the published image, as a user would*. On a fresh runner with no registry login it pulls the image the run has just pushed, checks it has the pushed digest, runs it with the `docker run` command from DEPLOYMENT.md (read-only root, no capabilities), checks readiness, the interface, that API documentation is off and that the API needs a sign-in, then smoke-tests it with Compose and the sandbox. The rollout job now waits for it. |
| Public access | An anonymous request (no GitHub account) read the image's manifest and tags from the registry: it is public, and built for `linux/amd64`. |
| Price check | Hetzner, DigitalOcean, AWS Lightsail, Google Cloud's Free Tier and Oracle's Always Free, read from the providers' pages on 2026-11-08 and recorded in PLAN.md §6 with their catches. |
| Documents | PLAN.md (status, price check, D62 and D63, blocker B3 resolved), DEPLOYMENT.md (the pipeline's verify step, the image, the status of option B and what setting it up would take), README. |

Nothing was created with any provider, and no secret was added to GitHub.

## Verification

### On GitHub

| Run | Result |
|---|---|
| Deploy #6 (4fca684) | **Passed** in 5 min 41 s: the whole of CI (Playwright "20 passed (1.8m)"), build, smoke test and publish, the new verify job (every step succeeded: pull signed out, the documented `docker run`, the Compose smoke test), and the rollout job's honest "Not rolled out: no server is configured". |

### The README, followed from a fresh clone (Windows 11, PowerShell)

A new clone of the public repository, in an empty directory, set up only with the README's own
commands:

| README step | Result |
|---|---|
| Backend: `python -m venv`, `pip install -c constraints.txt -e ".[dev]"` | Installed. |
| Copy `.env.example`, `cloudops generate-token`, put the token in `.env` | A 43-character token; the example's `CLOUDOPS_ALLOWED_TARGETS=localhost:8310` was already right for the sandbox. |
| `cloudops serve --port 8300` | Ready: database, schema and worker `ok`. |
| Install and start the sandbox | Answering on port 8310. |
| Register `sandbox-orders` with the README's `Invoke-RestMethod` command, switch the sandbox to `errors` | **Degraded 30 s later**, after two failing checks at the 15 s interval, as the README says; an incident opened. |
| Switch back to `healthy` | Recovered two checks later; the events read `application.registered`, `health.degraded`, `health.recovered`. |
| Create an ingest key; restart the sandbox with it and `SANDBOX_TRAFFIC_RPS=2` | Its request logs arrived: the Logs screen showed a full first page of 100 entries. |
| Frontend: `npm ci`, `npm start` | Served on port 4300, passing `/api` to the server. Signing in with the token, the overview, the application page (evidence timeline, checks, the 5xx responses from the request logs) and the logs worked in headless Chromium, with no script or console errors. |
| The server's own log | 200 JSON lines, none at error level, and neither the operator token nor the ingest key in any of them. |

Restarting the sandbox for the log step caused one refused connection, recorded as one failed check;
the state correctly stayed healthy, because two failures in a row are needed.

## Observed, not changed

- On Windows, a check of `http://localhost:8310/health` takes about 265 ms although the sandbox
  answers in a few: `localhost` is tried over IPv6 first, where the sandbox does not listen, and the
  IPv4 address is raced in 250 ms later (D21; before Phase 3's fix it was about 2 s). Using
  `127.0.0.1` in the URL and the allowlist avoids it. The README keeps `localhost`, which works.

## Not established

- **Running on a server.** Option B is written in `deploy.yml` but has never run. The server-side
  `cloudops-deploy` command and the TLS proxy configuration were not written, because they could
  not be tested without a server; DEPLOYMENT.md §6 lists what setting one up would take.
- **The Docker steps of the README on this machine.** Docker is not installed here; the image and
  Compose are verified in GitHub Actions only, now including the published image.
- **The README on Linux or macOS.** The fresh-clone run used Windows and PowerShell; CI covers the
  same commands' Linux equivalents only in its own scripted form.

## Known limitations

Unchanged from Phase 8 (SECURITY.md §10), plus: CloudOps Monitor is not running anywhere public,
so a visitor can see it only by running it themselves or through the screenshots Phase 10 will add.
