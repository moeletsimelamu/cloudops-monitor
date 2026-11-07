# CloudOps Monitor — Security

> **Status:** threat model and controls. **Implemented and tested in Phase 2:** operator
> authentication (§4: sessions, constant-time token check, sign-in rate limit, Origin and Host
> guard), secrets handling (§5: blank and placeholder tokens refused, `set`/`not set` display,
> redaction of CloudOps's own logs), the error envelope (§8) and security headers (§9). The
> target policy (§3) followed in Phase 3, and ingest keys, limits and redaction (§7) and the
> read-only GitHub integration (§5, §6) in Phase 4, and container controls and dependency audits
> (§9) in Phase 6. **Phase 8 reviewed every item**; what it found and changed is in
> [REVIEW.md](REVIEW.md), and §10 lists what remains.

## 1. Deployment assumptions

| Assumption | Consequence |
|---|---|
| First release: one operator, run locally or on a single host they control. | There is one operator role and no per-user permissions. |
| The server listens on `127.0.0.1` in development. The container publishes on the host's loopback (`-p 127.0.0.1:8300:8300`) unless an HTTPS reverse proxy sits in front. | Exposing it more widely requires `CLOUDOPS_AUTH_MODE=token` and TLS at the proxy. |
| Monitored applications are owned or operated by the same person or team. | The target allowlist is set by the operator through environment variables, not by API callers. |

Single-operator token auth is **not** a production multi-user security model. It has no individual
accounts, no audit of who did what, no MFA, and no fine-grained roles.

## 2. Assets and threats

| Asset | Threat | Planned control |
|---|---|---|
| The network around the monitor: cloud metadata, admin panels, databases | **SSRF**: a registered "health URL" makes the monitor request an internal service | Target policy (§3) |
| Operator actions (register targets, rotate keys, sync) | Unauthenticated or cross-site use | Operator token → HttpOnly SameSite=Strict session, `Origin` check on unsafe methods (§4) |
| GitHub token, operator token, ingest keys | Leak through logs, API responses, the config screen or the repository | `SecretStr`, redaction filter, `set/not set` display, hashed ingest keys, `.env` ignored by git (§5) |
| Log store | Secrets or personal data shipped by applications; storage exhaustion | Redaction, size limits, retention (§7) |
| Deployment pipeline | Arbitrary command execution, unauthorised deploys | No command execution and no deployment trigger in the first release (§6) |
| The monitor's availability | Slow or hostile targets, oversized responses, huge ingest batches | Timeouts, deadlines, a concurrency cap, a 64 KiB body read limit, request size limits |

## 3. Monitoring targets and SSRF

### 3.1 Policy

`CLOUDOPS_TARGET_POLICY`:

- **`allowlist`** (default). A target's `host:port` must match an entry in
  `CLOUDOPS_ALLOWED_TARGETS`, for example `localhost:8310,sandbox:8310,api.example.com:443`. Entries
  are an explicit operator decision, so an allowlisted host **may** resolve to a private or
  loopback address. That is how the local sandbox, OpsHub (`localhost:5050`) or DocuMind
  (`localhost:8000`) can be monitored.
- **`public`**. Any host whose every resolved address is a public unicast address, **plus** the
  allowlist.

Always enforced, whatever the policy:

- Scheme is `http` or `https`. No user-info (`user:pass@`) in URLs. URL at most 2,048 characters.
  No fragments.
- Ports must be 1–65535 and match the allowlist entry (allowlist mode), or be 80, 443 or
  1024–65535 (public mode).
- **Never reachable, even when allowlisted:** link-local `169.254.0.0/16` and `fe80::/10`,
  including the cloud metadata service at `169.254.169.254` and `fd00:ec2::254`; `0.0.0.0/8` and
  `::`; multicast; broadcast; reserved ranges. IPv4-mapped IPv6 addresses (`::ffff:a.b.c.d`) are
  unwrapped before checking. IPv6 addresses that lead to an IPv4 address (NAT64 `64:ff9b::/96`,
  6to4, Teredo and the deprecated `::a.b.c.d` form) are checked both as themselves and as that IPv4
  address, so `64:ff9b::a9fe:a9fe` (the metadata service through a NAT64 gateway) is blocked too.
- In public mode, these are blocked as well: loopback, RFC 1918, CGNAT `100.64.0.0/10`, unique-local
  `fc00::/7`, the deprecated site-local `fec0::/10`, documentation ranges and benchmark ranges.

### 3.2 Enforcement points

1. **At registration or update.** The URL is parsed and validated. The hostname must match the
   allowlist, or resolve to public addresses only (public mode). A 422 response explains which rule
   failed.
2. **At every check.** Resolution happens inside the HTTP client's network backend. Each resolved
   address is checked, and the socket connects to the address that was checked. A DNS answer that
   later changes to an internal address (DNS rebinding) is therefore caught at connection time. The
   check is recorded as `inconclusive / target_blocked` and an event is raised.
3. **Redirects are never followed.** A 3xx is recorded as `warn / unexpected_redirect` with the
   `Location` host in the detail.
4. **Response handling.** At most 64 KiB of the body is read, and only to parse a JSON `status`
   field. Response bodies are never stored or returned to the browser. Only the status code, timing,
   reason and a short detail are kept.

### 3.3 What the Phase 3 tests cover

Every structural URL rule; every always-blocked range, including IPv4-mapped forms and the IPv6
metadata address; allowlist matching (case, trailing dot, bracketed IPv6); public-mode refusal of
private, CGNAT, unique-local and documentation addresses; and, over real sockets, a public name
re-resolving to a private address, an allowlisted name resolving to the metadata address,
redirects not followed, the 64 KiB body cap and the hard deadline. When a name has several
addresses, **all** of them are checked before any connection attempt. One blocked address blocks
the check, even if the others are fine.

### 3.4 Phase 8 review and remaining limitations

The review re-read both enforcement points and tried every IPv6 form that can carry an IPv4 address.
It found that NAT64, IPv4-compatible, 6to4, Teredo and site-local addresses passed the public-mode
check, and that NAT64 for the metadata address passed even for an allowlisted name (REVIEW.md, F4).
They are now blocked at registration and at connection time, with tests that fail on the old code.
DNS rebinding is covered by tests that change the DNS answer between registration and the check,
for IPv4 and IPv6 answers.

- An allowlisted hostname is trusted to point wherever its DNS points, except at the always-blocked
  ranges. The allowlist is only as good as the operator's control of those names.
- An open proxy on an allowed host could still be used to reach other places. Do not allowlist
  proxies.
- The tests give the probe IPv6 answers through its resolver; no check has been made over a real IPv6
  network, because neither this machine nor the CI runners have one configured.

## 4. Authentication and authorisation

- `CLOUDOPS_AUTH_MODE=token` (the default in the Docker image and in `.env.example`) protects every
  `/api/*` route except `/api/health/live`, `/api/health/ready` and `/api/ingest/*`, which use
  ingest keys.
- The sign-in page exchanges the operator token for a random session ID. The cookie is HttpOnly,
  `SameSite=Strict`, and `Secure` when served over HTTPS (directly, or behind a trusted proxy). The
  session ID itself is never stored (see below), and sessions expire after 12 h. The token comparison is constant-time. Failed sign-ins are
  rate-limited (5 per minute per client).
- Failed sign-ins **and wrong bearer tokens** share that limit (Phase 8: before, a bearer token could
  be guessed without any limit).
- The database stores an **HMAC of each session ID keyed with the operator token**, never the ID or
  anything derived from the token alone. A session works only while the token it was signed in with
  is configured, so rotating `CLOUDOPS_OPERATOR_TOKEN` and restarting ends every session. *Sign out
  everywhere* on the System page (`DELETE /api/auth/sessions`) ends them all without a rotation.
- `X-Forwarded-For` and `X-Forwarded-Proto` are believed only from `CLOUDOPS_TRUSTED_PROXIES`
  (empty by default). Behind a TLS proxy listed there, the cookie is `Secure` and limits count per
  client; nobody else can claim another address or HTTPS. `cloudops serve` turns off uvicorn's own
  default, which trusted these headers from `127.0.0.1`, and its `server` header.
- Every request body is limited while it is received: 64 KiB, or 256 KiB for log batches. Without
  this, anyone who could reach the server could make it hold a body of any size in memory, because
  the framework reads the body before authentication is checked.
- `POST`, `PATCH` and `DELETE` requests must carry an `Origin` header that matches the configured
  public origin. Together with SameSite=Strict, this protects against CSRF.
- `CLOUDOPS_AUTH_MODE=disabled` is accepted for local development. It is shown in a banner and in
  the logs.
- Start-up is refused when token mode has no token, a token shorter than 32 characters, or the
  placeholder value from `.env.example`. The error message never repeats the value.
- The failed sign-in limiter is in memory and per process. That matches the single-process
  deployment, but it resets on restart.

## 5. Secrets

| Secret | Where it lives | Never… |
|---|---|---|
| `CLOUDOPS_OPERATOR_TOKEN` | environment / `.env` (git-ignored) / host secret store | logged, returned, or stored in the database |
| `CLOUDOPS_GITHUB_TOKEN` (fine-grained, read-only, selected repositories) | same | logged, returned; it is sent only to `api.github.com` |
| Ingest keys (`cok_…`) | shown once at creation or rotation; the database stores SHA-256 | stored in plain text |
| CI and deployment credentials | GitHub Actions secrets / environments | committed or echoed in workflow logs |

`.env.example` contains placeholders only. The configuration endpoint reports secrets as
`set`/`not set`. A logging filter masks known secret values and credential-shaped strings before
any log line is written, in the message, in every structured field at any depth, and in tracebacks.
Phase 8 scanned the whole Git history (23 commits) for credential patterns and sensitive file
names: it holds only labelled test values and AWS's documented example key, and no `.env`,
database or key file was ever committed.

### In the browser

The frontend never stores the operator token: it sends it once at sign-in and then relies on the
HttpOnly session cookie, which scripts cannot read. A new ingest key is held only in memory while it
is on screen, and is gone after a reload. `localStorage` holds only the theme and time-zone choice.
The frontend does not insert HTML from data, so logged text and event messages render as text, and it
follows only `https://` links from event details.

## 6. Deployment execution

- The application never runs shell commands, whether supplied by users or derived from
  configuration.
- The first release has no deployment trigger. Deployments run in GitHub Actions, where secrets
  live in GitHub environments, and CloudOps reads the results with a read-only token.
- Workflows use least-privilege `permissions:` blocks and pin every action, GitHub's own included,
  to a full commit SHA (with the version in a comment). Secrets reach steps only through `env:`,
  never inside a script, and pull requests run with `pull_request`, so a fork never sees them. The
  deploy workflow refuses to run (and says why) when its target configuration is absent.

## 7. Logging hygiene

- CloudOps's own logs never include request bodies, cookies, `Authorization` headers or query
  strings of sign-in requests.
- Ingested logs are redacted before storage (see [ARCHITECTURE.md §5.1](ARCHITECTURE.md)): values
  under sensitive attribute names, bearer and basic credentials, JWTs, GitHub, AWS, Slack, Stripe
  and Google keys, CloudOps ingest keys, PEM private keys (even cut short), credentials in URLs and
  `password=`-style pairs. The limitation: pattern-based redaction cannot catch every secret or
  every piece of personal data. Applications remain responsible for not logging them.
- Retention is 7 days or 200k rows by default, whichever is reached first.

## 8. Input validation and errors

Every request and ingest payload has a Pydantic schema with bounds: URL rules (above), slugs
`^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$`, intervals 15–3600 s, timeouts 1–30 s, page size 1–200, time
ranges in ISO-8601 with `from < to` and at most 30 days for history queries (7 days for logs;
deployment history may be left open-ended, and is paged),
severity and outcome enums, and `owner/repo` and workflow file name patterns.

All errors use one envelope: `{"error": {"code", "message", "details", "request_id"}}`. Unhandled
exceptions return a generic 500 with the request ID. Stack traces go only to the server log.
Validation errors give the location and the rule, never the rejected value. Request bodies are
limited in size before they are parsed (§4).

## 9. Containers and dependencies

- Multi-stage image, `python:3.14-slim` runtime, non-root user (uid 10001), application files
  owned by root so the server cannot change them, data only in the `/data` volume. No build tools,
  source tree, tests, docs or `.env` files in the final image. CI checks each of these on the built
  image rather than trusting the Dockerfile.
- At run time (`compose.yaml` and the documented `docker run`) the root file system is read-only
  with an in-memory `/tmp`, every Linux capability is dropped and privilege gain is disabled. CI
  checks this on the running containers: read-only root, an empty capability bounding set, no
  write outside `/data`.
- Ports are published on `127.0.0.1` in `compose.yaml` and in the documented `docker run` command.
  Anything reachable from other machines needs a TLS proxy in front (DEPLOYMENT.md §6).
- The images take secrets only at run time. CI uses throwaway tokens made from the run ID and
  checks that the operator token never appears in the container's logs.
- Direct dependencies are pinned in `pyproject.toml` and `package.json`. Transitive dependencies are
  pinned in `constraints.txt` and `package-lock.json`. CI fails if `pip-audit` finds a known
  vulnerability in any pinned Python package, or `npm audit --omit=dev` one of moderate severity or
  above in what ships to the browser. Development-only npm tools are not audited in CI; on
  2026-11-07 they had 16 moderate advisories and none high.
- Publishing uses the workflow's own short-lived `GITHUB_TOKEN` with `packages: write` for that
  job only. CI's token also has `actions: read`, so the container smoke test can read this
  repository's workflow runs; CI checks it never appears in the server's logs.
- A setting the server cannot parse is reported by variable name only: the rejected value is never
  repeated, in case it was a secret. Any token present in the environment is masked in that line
  as well. A server rollout, if ever configured, uses a deploy-only SSH key and a pinned host key
  from the `production` environment.
- Security response headers: CSP without `unsafe-inline` for scripts, `X-Content-Type-Options`,
  `Referrer-Policy`, `frame-ancestors 'none'`.

## 10. Known limitations

Found or confirmed in the Phase 8 review. None is a defect with a fix that fits this release; each
is a property of the design or of what could be tested here.

| Limitation | Consequence | What would address it |
|---|---|---|
| One shared operator token, no user accounts | No record of who did what, no MFA, no roles. Anyone with the token is the operator. | User accounts with an identity provider; out of scope for this release (§1). |
| The failed sign-in limit is per process and in memory | A restart clears it. It is per client address, so many addresses can each guess 5 times a minute; the 32-character minimum, not the limit, is what makes guessing infeasible. | A persistent limiter, if guessing were a realistic threat. |
| Someone else's failed sign-ins from the same address lock the operator out for up to a minute | Possible when several people share an address, or behind a proxy that is not in `CLOUDOPS_TRUSTED_PROXIES`. | List the proxy; then counts are per client. |
| Ending all sessions does not revoke the token | A person who knows the token can sign in again. | Rotate the token if it may have leaked (DEPLOYMENT.md §3). |
| Ingest keys are not rate-limited when wrong | 43 random characters make guessing infeasible; a wrong key costs one hashed lookup. | — |
| Pattern-based log redaction | A secret in an unknown format, or personal data, can still be stored if an application logs it. | Applications must not log secrets; redaction is a safety net. |
| The SSRF allowlist trusts DNS for listed names | A listed name pointing somewhere unexpected (other than always-blocked ranges) is followed. | Allowlist names you control, or IP addresses. |
| Python packages are pinned by version, not by hash | A compromised package index could serve a different file for the same version. | `--require-hashes` with a hashed constraints file. |
| Base images are pinned by tag (`python:3.14.9-slim-trixie`, `node:22.14.0-bookworm-slim`), not digest | A re-pushed tag would change the image. Tags of exact patch versions are rarely re-pushed except for security rebuilds. | Pin digests, with automated update pull requests. |
| No automated dependency update pull requests | New advisories are found by the CI audit, which fails, rather than fixed ahead of time. | Dependabot or Renovate (not enabled: it would open pull requests on the public repository). |
| Development-only npm packages have known moderate advisories (16 on 2026-11-08, none high) | They run on developer machines and CI, never in the browser bundle or the image. | Upgrading the Angular toolchain when fixed versions ship. |
| Not tested with a screen reader | Automated checks (axe at every impact level, keyboard tests) pass, but what a screen reader announces has not been heard. | A manual pass with NVDA or VoiceOver. |
| No HTTPS of its own | Without a TLS proxy, the token and the session cookie cross the network in clear text. | Never expose it without a TLS proxy (DEPLOYMENT.md §6). |
