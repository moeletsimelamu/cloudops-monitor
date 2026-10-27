# Phase 2 — Backend foundation

**Date:** 2026-10-27

## What was built (`backend/`)

| Area | Implementation |
|---|---|
| Packaging | `pyproject.toml` (direct dependencies pinned exactly) and `constraints.txt` (all 41 resolved packages, from `pip freeze` on CPython 3.14.3). Console script `cloudops`. |
| Configuration | `config.py`: `CLOUDOPS_*` settings with bounds. Comma-separated lists. Blank secrets count as unset. `check_settings()` separates fatal errors from warnings: the app refuses to start on a missing, short or placeholder operator token, malformed allowlist entries, or a non-HTTPS GitHub API URL. Error messages never contain secret values. |
| Logging | `logging_setup.py`: JSON lines on stdout (or `text`). Request ID on every record, structured fields, and redaction of configured secrets and credential patterns in messages, fields and exception text. |
| Redaction | `domain/redaction.py`: bearer/basic credentials, JWTs, GitHub tokens, AWS key IDs, ingest keys, URL user-info, `password=`/`"api_key": …` pairs, and sensitive attribute names. Phase 4 reuses it for ingested logs. |
| Errors and middleware | Uniform error envelope; validation errors never echo input; unhandled errors give a generic 500 with the request ID. Request IDs, an access log (path only), security headers (CSP per route type), a Host allowlist (DNS rebinding) and a cross-site write guard (`Origin`/`Sec-Fetch-Site`). |
| Database | SQLAlchemy 2.1 models for every first-release table, with check constraints, indexes, a partial unique index for "one open incident per application", and a `UTCDateTime` type that rejects naive datetimes. The Alembic baseline revision was generated with `alembic revision --autogenerate` and reviewed. Migrations run on a dedicated engine (FKs off, real transactional DDL) at start-up or with `cloudops db upgrade`. |
| Health endpoints | `GET /api/health/live` (no DB access) and `GET /api/health/ready` (DB `SELECT 1` and schema at head; 503 with check names otherwise). |
| Authentication | Operator token → session cookie (HttpOnly, SameSite=Strict, `Path=/api`, Secure over HTTPS, hash stored, 12 h expiry). Bearer token for scripts. Failed sign-ins limited to 5 per minute per client. Disabled mode for local development. `GET /api/system/config` is the first protected route. |
| CLI | `cloudops db upgrade`, `cloudops db backup PATH` (SQLite online backup; refuses to overwrite), `cloudops generate-token`. |
| Example configuration | `.env.example` with placeholders. Its placeholder token is refused at start-up. |

## Verification (commands actually run)

| Command (from `backend/`) | Result |
|---|---|
| `.venv/Scripts/python -m pytest` | **95 passed** (auth 13, cli 4, config 14, db 11, errors/middleware 12, health 5, logging 4, redaction 30, system config 2) |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 35 files already formatted |
| `mypy` (strict) | No issues in 35 source files |
| `uvicorn cloudops.main:create_app --factory --port 8300` (fresh data directory) | Started; baseline migration applied ("Will assume transactional DDL"); JSON log lines; `curl` checks below |

Manual HTTP checks against the running server: live 200; ready 200 with `database: ok, schema:
ok`; `/api/system/config` 401 without credentials, 200 with the bearer token and with the session
cookie after `POST /api/auth/session` (cookie attributes as above); cross-origin POST 403; unknown
Host header 400; `/api/docs` 200. The server log showed no token values.

## Defects found and fixed during the phase

- **Log lines were dropped under the test runner.** The handler kept a reference to the
  `sys.stdout` that existed at start-up, and that stream had since been closed ("I/O operation on
  closed file"). The handler now looks up `sys.stdout` when it writes.
- **JSON-style secrets were not redacted.** `"api_key": "…"` slipped past the `key=value`
  pattern because of the closing quote. Fixed and covered by a test.
- **Readiness probes logged Alembic start-up messages on every call.** The current revision is now
  read directly, and the head revision is cached.
- **Alembic reported "non-transactional DDL".** The migration engine already issued real
  transactions, so `transactional_ddl=True` is now set explicitly, and a test proves DDL rolls back.
- **A copied `.env.example` would have run with a publicly known token.** The placeholder is now
  refused, and a blank `CLOUDOPS_GITHUB_TOKEN=` no longer counts as "set".

## Known limitations

- Uvicorn's own start-up lines ("Started server process…") are plain text, not JSON. Phase 6 will
  pass a logging configuration to uvicorn in the container.
- The readiness check has no worker check yet. It arrives with the worker in Phase 3.
- The sign-in limiter is per process and in memory.
- Not run here: Python 3.12 (CI will run the 3.12/3.14 matrix in Phase 6), Linux, Docker.
