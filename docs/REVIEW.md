# CloudOps Monitor — Security and quality review

**Phase 8, 2026-11-08.** A review of the code as it stood after Phase 7 (`ff20b53`), against the
brief's list: SSRF protection, authentication and authorisation assumptions, secrets management,
input validation, log redaction, container configuration, dependency risks, error handling,
accessibility and responsive behaviour. [SECURITY.md](SECURITY.md) describes the controls; this
file records what was checked, what was found and what changed.

## How it was reviewed

- **Reading:** every module on a trust boundary: authentication and sessions, the request
  middleware, settings, the SSRF policy and probe, log ingestion and redaction, the GitHub client,
  static file serving, error handling, the Dockerfiles, Compose and both workflows.
- **Trying it:** each suspected problem was first reproduced against the unchanged code, by a
  script or a test, before anything was changed. The results quoted under each finding come from
  those runs.
- **Routes:** every operation in the OpenAPI document (31, with the one added in this phase) was
  called without credentials. All 25 operator operations answered 401, and so did log ingest, which
  needs an ingest key instead. Only the session endpoint (`/api/auth/session`: status, sign-in,
  sign-out) and the two health probes answer without one, as designed.
- **History:** all 23 commits were searched for credential patterns (GitHub, AWS, Slack, Stripe,
  JWT, PEM, ingest keys, token-like assignments) and for sensitive file names. Only labelled test
  values and AWS's documented example key appear, and no `.env`, database or key file was ever
  committed. Every commit has the same author, and none carries an AI attribution trailer.
- **Dependencies:** `pip-audit` on the backend's pinned packages and `npm audit` (2026-11-08).
- **Accessibility:** axe at every impact level on every screen, at 320 and 1280 px, in both
  themes; keyboard use; the new controls looked at in screenshots at 320 and 1440 px.

## Findings

Severity is for this product as deployed: one operator, normally on a private address or behind a
TLS proxy.

| # | Finding | Severity | Status |
|---|---|---|---|
| F1 | Sessions survived a rotation of the operator token, and there was no way to end them all | Medium | Fixed |
| F2 | Wrong bearer tokens were not counted: the API allowed unlimited guessing of the operator token | Low | Fixed |
| F3 | Request bodies of any size were read into memory before authentication, on every path but log ingest | Medium | Fixed |
| F4 | IPv6 addresses that lead to an IPv4 address passed the SSRF check (NAT64 of the metadata service, of loopback, of private ranges) | Medium | Fixed |
| F5 | Forwarded headers were believed from `127.0.0.1` but not from a real proxy: spoofable locally, and behind TLS the cookie was not `Secure` and every client shared one sign-in limit | Medium | Fixed |
| F6 | Log redaction skipped nested values in CloudOps's own log records, and private keys | Low | Fixed |
| F7 | Containers ran with a writable root file system and default capabilities; workflows referenced actions by moving tags | Low | Fixed |

### F1 — Sessions outlived a token rotation

**Found:** a session stored only the SHA-256 of its ID, unrelated to the operator token. A cookie
issued before the token was rotated still worked after the restart (reproduced: `200`).
DEPLOYMENT.md already listed this as an open item from Phase 6.

**Fix:** the stored value is now an HMAC of the session ID keyed with the operator token. A session
works only while the token it was signed in with is configured, so rotating the token ends every
session without anything to remember or clean up. `DELETE /api/auth/sessions` and a *Sign out
everywhere* button on the System page (with a confirmation) end them all without rotating.
Sessions stored before this change stop working once, at the upgrade.

**Tests:** unit (the stored digest, rotation, signing out everywhere from two clients, the endpoint
needing the operator), a system test that rotates the token across a real restart (the old cookie
and the old bearer token are refused, the new token works), a component test of the button and a
browser test with two separate browsers.

### F2 — Unlimited bearer-token guessing

**Found:** the failed sign-in limit (5 a minute) applied to the sign-in form only. After 20 wrong
bearer tokens, the right one still answered `200`. The 32-character minimum makes guessing
infeasible, so the risk was low, but the limit was not what the documentation implied.

**Fix:** wrong bearer tokens count against the same per-client limit as failed sign-ins, and a
limited client gets `429` with `Retry-After` on both.

### F3 — Unbounded request bodies

**Found:** only `/api/ingest/logs` limited its body. FastAPI reads a JSON body before it resolves
dependencies, so authentication came after the read: an unauthenticated 20 MiB `POST` to the
sign-in endpoint was read in full and answered `422`. Anyone who could reach the server could make
it hold bodies of any size in memory.

**Fix:** every path is limited while the body is received: 64 KiB by default, 256 KiB for log
batches. A declared `Content-Length` above the limit is refused before any body is read; a body
without one (chunked) is counted as it arrives.

**Tests:** unit tests for open, operator and chunked requests; a system test sends only the headers
of an 8 MiB request to the real server and gets `413` straight away, and the server stays ready.

### F4 — IPv6 transition addresses passed the SSRF check

**Found:** Python's `is_global` treats `64:ff9b::/96` (NAT64) and `::a.b.c.d` (IPv4-compatible) as
public, and only IPv4-mapped addresses were unwrapped. In public mode, `64:ff9b::7f00:1` (loopback
through NAT64) and `64:ff9b::a9fe:a9fe` (the metadata service) were allowed, and the latter was not
caught by the always-blocked list either, so an allowlisted name resolving to it would have been
followed. Site-local `fec0::/10` also passed. These matter on an IPv6-only network with a NAT64
gateway, which some cloud providers offer.

**Fix:** NAT64, 6to4, Teredo and IPv4-compatible addresses are checked as themselves and as the
IPv4 address they lead to; either being blocked blocks the connection. NAT64 to a public address is
still allowed. Site-local is blocked in public mode.

**Tests:** 9 new address cases (8 fail on the old code) and two DNS answers at connection time, for
public mode and for an allowlisted name.

### F5 — Forwarded headers trusted from the wrong place

**Found:** uvicorn believes `X-Forwarded-For` and `X-Forwarded-Proto` from `127.0.0.1` by default.
Locally, any process could claim a new address for every sign-in guess. Behind a TLS proxy in front
of the container (Phase 9's plan), the proxy is not `127.0.0.1` inside the container, so the
opposite happened: the cookie was never marked `Secure`, and every client shared the proxy's
address, so anyone could lock the operator out of signing in. The `server: uvicorn` header was also
sent.

**Fix:** `CLOUDOPS_TRUSTED_PROXIES` (empty by default) lists the proxies whose forwarded headers
are believed; the application applies it, and `cloudops serve` turns uvicorn's own handling and its
`server` header off. A list entry must be an address or network, and "everyone" (`0.0.0.0/0`, `*`)
is refused at start-up. The System page shows the setting.

**Tests:** unit tests with and without a trusted proxy, and system tests against the real server:
forwarded headers ignored by default (no `Secure`, no new address per guess, no `server` header) and
believed from a listed proxy (a `Secure` cookie, and the operator not locked out by someone else's
guesses).

### F6 — Gaps in log redaction

**Found:** structured fields of CloudOps's own log records were redacted only when they were
strings, so a token inside a list or a nested object was written as it was (reproduced with a
GitHub token in a list). PEM private keys were not recognised, nor Slack, Stripe or Google keys.

**Fix:** redaction applies at any depth, and those formats are masked; a private key is masked to
its end line, or to the end of the text if it was cut short.

### F7 — Container runtime and workflow pinning

**Found:** the images already ran as non-root users with app files they cannot change, but the
containers had a writable root file system and Docker's default capabilities. Workflows referenced
`actions/*` by major-version tags, which can move.

**Fix:** Compose (and the documented `docker run`) use a read-only root file system with an
in-memory `/tmp`, drop every capability and disable privilege gain; CI checks this on the running
containers. Every action is pinned to the commit its tag pointed to on 2026-11-08, with the version
in a comment.

## Reviewed and found sound

- **CSRF and DNS rebinding:** the Host allowlist and the `Origin`/`Sec-Fetch-Site` check on unsafe
  methods, with SameSite=Strict, HttpOnly cookies scoped to `/api`.
- **Token comparison** is constant-time over digests; start-up refuses missing, short and
  placeholder tokens; secrets are never returned (`set`/`not set`).
- **Ingest keys** are random, shown once, stored as SHA-256 and checked by prefix and hash; a key can
  only write to its own application.
- **Errors** never include stack traces or rejected values; unhandled errors are a generic `500` with
  the request ID.
- **Static files** are served only from inside the build directory (resolved paths), with
  `nosniff` and explicit media types.
- **Frontend:** no HTML is inserted from data; external links are followed only for `https://`
  (checked on the server and in the browser); `localStorage` holds only display preferences.
- **GitHub:** the client follows no redirects and ignores proxy settings, so the token only reaches
  the configured `https` API host. Secrets in workflows reach steps through `env:` only, and pull
  requests from forks do not get them.
- **Dependencies (2026-11-08):** no known vulnerabilities in the backend's pinned Python packages or
  in the npm packages that ship to the browser. Development-only npm tools had 16 moderate
  advisories and none high, as recorded in Phase 6.

## Accessibility and responsive behaviour

- axe at every impact level, not only serious and critical: no findings on the sign-in page, the 12
  signed-in views and an open confirmation dialog, at 320 and 1280 px in light and dark.
- No horizontal page scrolling at 320 px, the width WCAG 1.4.10 uses for 400 % zoom of a 1280 px
  window.
- Keyboard: the skip link is the first stop and moves focus to the content; navigation links work
  with Tab and Enter; a confirmation dialog opens on its harmless choice, never lets Tab reach the
  page behind it, and gives focus back to its button on Escape. These are now browser tests in CI.
- Not done: a pass with a real screen reader (SECURITY.md §10).

## What remains

The limitations that are properties of the design, or that could not be tested here, are listed in
[SECURITY.md §10](SECURITY.md#10-known-limitations).
