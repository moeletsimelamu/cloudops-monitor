# CloudOps Monitor — Deployment

> **Status:** Phase 6. The container image, Docker Compose setup, CI and the delivery workflow exist
> and run in GitHub Actions. Running on a server (option B) is written but has never been used: no
> server exists, and creating one is a decision for Phase 9.

## 1. What is delivered

| Artefact | What it is |
|---|---|
| `Dockerfile` | One image: the API, the monitoring worker and the built Angular app in one process. Python 3.14 slim, non-root user `cloudops` (uid 10001), data in the `/data` volume, no secrets inside. |
| `sandbox/Dockerfile` | The sandbox test target, for Compose and the smoke test. Non-root (uid 10002). |
| `compose.yaml` | CloudOps Monitor plus the sandbox, both published on `127.0.0.1` only. |
| `scripts/smoke.py` | Takes a running stack through the primary workflow over HTTP (§4). Standard library only. |
| `.github/workflows/ci.yml` | Checks on every branch and pull request (§5). |
| `.github/workflows/deploy.yml` | On every push to `main`: CI, then build, smoke-test and publish the image, then roll out if a server is configured (§5). |

The image is `ghcr.io/moeletsimelamu/cloudops-monitor`, tagged `sha-<first 12 characters of the
commit>` and `latest`. It is public: on 2026-11-07 an anonymous request listed its tags, so
`docker pull` needs no sign-in.

## 2. Option A — run it on your own machine

This is the supported way to run CloudOps Monitor, and it costs nothing.

### With Docker Compose (CloudOps and the sandbox)

Create a file named `.env` next to `compose.yaml` (it is git-ignored) with two random values:

```
CLOUDOPS_OPERATOR_TOKEN=<at least 32 random characters>
SANDBOX_CONTROL_TOKEN=<another random value>
```

Either can come from:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then build and start both services:

```bash
docker compose up --build -d
```

Open http://localhost:8300, sign in with the operator token, and register an application with the
health URL `http://sandbox:8310/health` (the name Compose gives the sandbox; it is already in
`CLOUDOPS_ALLOWED_TARGETS`). To break it on purpose:

```bash
curl -X PUT http://127.0.0.1:8310/control/mode -H "X-Sandbox-Token: <SANDBOX_CONTROL_TOKEN>" -H "Content-Type: application/json" -d "{\"mode\":\"errors\"}"
```

For logs and request metrics as well, create an ingest key for the application in its
*Configuration* tab, add `SANDBOX_INGEST_KEY=<key>` and `SANDBOX_TRAFFIC_RPS=2` to `.env`, and run
`docker compose up -d` again.

### With the image alone

```bash
docker run -d --name cloudops -p 127.0.0.1:8300:8300 -v cloudops-data:/data -e CLOUDOPS_OPERATOR_TOKEN=<token> -e CLOUDOPS_ALLOWED_TARGETS=<host:port,...> ghcr.io/moeletsimelamu/cloudops-monitor:latest
```

`.env.example` lists every setting. The image sets `CLOUDOPS_DATA_DIR=/data`,
`CLOUDOPS_STATIC_DIR=/app/static`, `CLOUDOPS_LOG_FORMAT=json` and turns the API documentation off.

## 3. Operating it

| Task | How |
|---|---|
| Is it up? | `GET /api/health/live` (the process runs) and `GET /api/health/ready` (database, schema and worker usable). Docker's own health check calls `ready` every 30 s. The *System* page shows the same and more. |
| Logs | `docker compose logs cloudops`: one JSON object per line, uvicorn's messages included. Secrets that are configured are masked; query strings are never logged. |
| It will not start | Exit status 2: a setting is invalid; the one log line (`config.invalid`) names every variable to fix, never its value. Exit status 3: the database cannot be used (`database.unusable` names the file and SQLite's reason, for example a damaged file or a directory it cannot write). |
| Writes fail with 503 | `database_busy`: something else held the database's write lock (a long manual `sqlite3` session, for instance). Nothing was changed; retry. If it lasts 30 s, readiness reports the worker as stalled until the lock is released. |
| Back up | `docker compose exec cloudops cloudops db backup /data/backup.db`, then `docker compose cp cloudops:/data/backup.db .` The backup uses SQLite's online backup, so the server can keep running. |
| Upgrade | Pull or build the new image and start it with the same volume. Schema migrations run at start-up (`CLOUDOPS_AUTO_MIGRATE=true`). Take a backup first. |
| Restore | Stop the service, copy the backup over `/data/cloudops.db` in the volume, start it again. |
| Rotate the operator token | Change `CLOUDOPS_OPERATOR_TOKEN` and restart. New sign-ins need the new token, but browsers already signed in stay signed in until their session expires (`CLOUDOPS_SESSION_HOURS`, 12 by default). There is no command yet to end them all at once; that is a Phase 8 review item. |
| Remove everything | `docker compose down -v` deletes the containers and the data volume. |

Data lives only in the volume. The image is rebuilt from source each time and holds nothing that
cannot be recreated.

## 4. What the smoke test proves

`scripts/smoke.py` runs against any running CloudOps Monitor and sandbox. In CI it runs against the
containers through Compose:

1. the server reports ready (database, schema, worker);
2. the app shell is served at `/` and at client routes, a missing asset is a 404, and the API
   documentation is off;
3. a foreign `Host` header and an anonymous request are refused;
4. a cloud metadata address is refused as a monitoring target;
5. the sandbox is registered and checked: healthy;
6. the sandbox is switched to failing: degraded, with an open incident;
7. switched back: healthy, the incident closed, both transitions recorded as events;
8. a log entry is shipped with an ingest key, found by search, with its password masked;
9. with `--github-repo`, an application linked to a repository's workflow is synced and its
   deployment records are checked to be real runs of that repository (in CI: this repository's own
   `deploy.yml`, read with the run's read-only token);
10. the monitoring worker is healthy;
11. the application is removed again.

CI then checks that every log line is JSON and that neither the operator token nor the GitHub token
appears in them, that Docker reports the container healthy, and that data survives a restart. Before
any of that, it starts the image with a too-short token and checks it exits with status 2 and one
JSON line that does not repeat the value.

## 5. The pipeline

**CI** (`ci.yml`) runs on every branch except `main`, on pull requests, and as the first stage of
every delivery:

| Job | What it checks |
|---|---|
| Backend · Python 3.12 and 3.14 | ruff lint and format (backend and `scripts/`), mypy strict, pytest, including the system tests that run the server as a real process (start-up failures, stop and restart, kill during a check, a held database lock, timeouts, time zones, retention under load; the retention measurements become notices on the run) |
| Sandbox | ruff, mypy, pytest |
| Frontend | Prettier, unit and component tests, production build |
| Browser end-to-end | Playwright against the production build served by the real server, with the sandbox: the operational journey, every screen at two sizes in both themes with axe, losing the API, a lost session, a UTC+14 time zone |
| Dependency audit | `pip-audit` on every pinned Python package; `npm audit` on the packages that end up in the browser bundle. Development-only npm packages are not checked here, because they never ship. |
| Container images | Build both images; non-root users, an empty data volume, no `.env` files and no source, tests or docs in the image, app files the server cannot change; an invalid setting refused with one JSON line; the smoke test, real deployment records included; JSON logs without either token; Docker's health check; data surviving a restart |

**Deploy** (`deploy.yml`) runs on every push to `main` and on demand, one at a time:

1. **CI**: the whole of `ci.yml`. Nothing else starts unless all of it passes.
2. **Publish**: build the image, smoke-test exactly that image with the sandbox, push it to GitHub
   Container Registry, and record the tags and digest in the run summary.
3. **Roll out**: only if a server is configured (option B). Otherwise the job ends successfully with
   a notice, "Not rolled out", naming what is missing. It changes nothing and claims nothing.

The sandbox application in the local demo can be linked to this repository's `deploy.yml`, so its
deployment history is the real list of these runs. With option A, a "deployment" means the image
was built, tested, smoke-tested and published. It does not mean it is running anywhere.

## 6. Option B — one small server (not set up)

The workflow can roll out to one Linux server running Docker, behind a TLS proxy. It needs, in the
repository's `production` environment:

| Name | Kind | Value |
|---|---|---|
| `DEPLOY_HOST` | secret | The server's host name |
| `DEPLOY_USER` | secret | An account that may run only the deploy command |
| `DEPLOY_SSH_KEY` | secret | A private key used for nothing else |
| `DEPLOY_KNOWN_HOSTS` | secret | The server's host key line, so the connection is verified rather than trusted on first use |
| `DEPLOY_URL` | variable | The public `https://` address, used to check readiness afterwards |

On the server, a `cloudops-deploy <image>` command pulls the image and restarts the service with the
same volume. The job then waits up to 150 seconds for `/api/health/ready`, and fails if the server
does not report ready.

**None of this has been run.** The server-side command, the proxy and the server itself belong to
Phase 9, after a price check and your decision. Expected costs, security and teardown are in
[PLAN.md](PLAN.md) §6. Nothing has been created.
