# Security review and controls

Reviewed: 2026-11-17 (Phase 7). Scope: API, worker, web client, deployment configuration.

## Findings fixed in this review

| Finding | Risk | Fix |
|---|---|---|
| Viewer accounts could create snapshots, run backtests, place paper orders and edit the journal | Least-privilege violation | Every data-changing endpoint now requires the admin role; a test checks 12 write endpoints return 403 for viewers |
| No password change or user management | Weak account hygiene | `POST /api/auth/password` (re-authenticates, revokes all other sessions); admin user management with immediate session revocation on role/status changes |
| Concurrent first-admin bootstrap could create two administrators | Privilege escalation race | Bootstrap takes a PostgreSQL advisory lock; a test fires two concurrent requests and gets exactly one success |
| An open WebSocket stayed authorised after logout | Stale session | The socket re-checks the session on every status tick and closes with 1008 once revoked |
| API responses had no CSP; no HSTS | Defence in depth | `Content-Security-Policy: default-src 'none'` on API responses; HSTS when cookies are secure |
| All dashboard traffic shared one per-IP rate limit behind the proxy (found in Phase 4) | Self-inflicted denial of service | Per-user limits for signed-in traffic; `X-Forwarded-For` trusted only from configured proxy addresses or CIDR ranges |

## Controls in place

- **Authentication**: Argon2id hashes; 12+ character passwords with three character classes;
  lockout after 5 failures; generic login errors; timing-equalised unknown-user path; one-time
  bootstrap, token-gated in production.
- **Sessions**: HS256 JWT in an httpOnly, SameSite=Strict (Secure in production) cookie; never
  readable by JavaScript; server-side revocation by token version; CSRF header required on
  cookie-authenticated writes.
- **Authorisation**: admin vs viewer roles; viewers are read-only.
- **Input validation**: Pydantic models with `extra="forbid"`, size limits on every body, file
  imports and list; prices validated against tick sizes; only whitelisted SQL fragments are
  interpolated (all values are bound parameters).
- **Rate limiting**: per user (signed in), per IP (anonymous), stricter on authentication and the
  copilot; Redis-backed with an in-process fallback.
- **Errors and logs**: unhandled errors return a generic 500 with a request ID; validation errors
  never echo submitted values; JSON logs redact sensitive keys; the audit log scrubs secrets.
- **Audit trail**: logins, bootstrap, logout, password and user changes, rule edits and
  verifications, snapshots, imports, backtests, paper resets, calendar changes, copilot questions.
- **Secrets**: only in server-side environment variables (`SecretStr`); production refuses
  insecure defaults; nothing secret is sent to the browser or the AI model.
- **Execution safety**: no live order path; code-scan tests for broker order endpoints and UI
  automation; the paper simulator and backtester are proven free of network clients by test.
- **AI copilot**: read-only tools only, labelled data classes, deterministic risk numbers, capped
  tool rounds, per-user rate limit, full audit of questions, answers and tool calls. Tool results
  can contain user-supplied text (e.g. calendar titles); because no tool can change anything, a
  prompt injection could at worst distort an answer, which the visible tool trace lets you check.
- **Dependencies**: `pip-audit` and `npm audit` run in CI; both report no known vulnerabilities
  at the time of review.
- **Transport and headers**: CSP and frame protection on web and API; HSTS in production; ports
  bound to localhost in Compose.

## Residual risks and recommendations

- Single-factor authentication. For exposure beyond a private network, put the site behind a VPN or
  an identity-aware proxy, or add TOTP.
- Account data is entered by hand; compliance labels are only as good as that data and the rule
  verification.
- Docker images have not been built and scanned here; run an image scanner (e.g. Trivy) in your
  pipeline.
- Database encryption at rest is left to the host (encrypted disk / managed PostgreSQL).
