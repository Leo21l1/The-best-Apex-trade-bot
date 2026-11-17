# Production deployment

PROJECT APEX is a single-trader application. "Production" here means running it on a machine you
control, reachable over HTTPS, with backups. It never needs inbound access to any broker.

> **Not yet exercised:** the Docker images and this guide have been written and syntax-checked
> (`docker compose config`), but have not been built or run in the development environment, which
> has no Docker daemon. Do a dry run on your own machine first.

## 1. Prepare secrets

```bash
cp .env.example .env
python3 -c "import secrets; print('APEX_SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env
python3 -c "import secrets; print('APEX_BOOTSTRAP_TOKEN=' + secrets.token_urlsafe(24))" >> .env
```

Then edit `.env`:

| Variable | Production value |
|---|---|
| `APEX_ENVIRONMENT` | `production` (the API refuses to start with weak or missing secrets) |
| `APEX_COOKIE_SECURE` | `true` |
| `APEX_CORS_ORIGINS` | `["https://apex.example.com"]` |
| `NEXT_PUBLIC_APEX_WS_URL` | `wss://apex.example.com/api/ws` (build argument of the web image) |
| `POSTGRES_PASSWORD` | a strong random value |
| `APEX_MARKET_DATA_PROVIDER` | `none`, or your licensed provider (and its key) |
| `APEX_ANTHROPIC_API_KEY` | optional, enables the copilot |

Keep `.env` out of version control (it is git-ignored) and readable only by the deploying user
(`chmod 600 .env`).

## 2. Run behind HTTPS

Serve everything from one origin so the session cookie, CSRF protection and WebSocket all work:
`/api/*` (including `/api/ws`) → API, everything else → web. An example Caddy config is in
`infrastructure/docker/Caddyfile`; it obtains certificates automatically. Any reverse proxy works
if it:

- terminates TLS and forwards WebSocket upgrades on `/api/ws`;
- sets `X-Forwarded-For` (the API trusts it only from `APEX_TRUSTED_PROXIES`);
- does not cache `/api/*` responses (the API sends `Cache-Control: no-store`).

Compose binds every port to `127.0.0.1`, so only the proxy is exposed.

## 3. Start and create the administrator

```bash
docker compose up -d --build
docker compose logs -f api        # wait for "startup"
```

Open the site and create the first administrator with the `APEX_BOOTSTRAP_TOKEN`, or from the
command line:

```bash
docker compose exec api python -m app.cli create-admin --email you@example.com
```

Add read-only viewers under **Settings → Users** if anyone else needs to look.

## 4. Upgrades

```bash
git pull
docker compose build
docker compose up -d   # the API runs `alembic upgrade head` before starting
```

Take a backup first. Migrations are forward-only in normal operation; each also has a downgrade.

## 5. Backups and restore

```bash
# daily, e.g. from cron on the host
PGHOST=127.0.0.1 PGUSER=apex PGPASSWORD=... PGDATABASE=apex scripts/backup-db.sh /var/backups/apex
# restore into an empty database
pg_restore --clean --if-exists --no-owner -d apex /var/backups/apex/apex-apex-<stamp>.dump
```

Back up the `.env` file separately and securely. Test a restore at least once.

## 6. Monitoring

- `GET /api/health/live` – process up; `GET /api/health/ready` – 503 if PostgreSQL or Redis is down.
- **System Health** page – worker heartbeat, data-feed state and freshness, rule verification.
- JSON logs on stdout (`docker compose logs`), with request IDs; secrets are redacted.
- Alerts in the app for feed failures, stale account data, risk thresholds and events.

## 7. Hardening checklist

- [ ] `APEX_ENVIRONMENT=production`, `APEX_COOKIE_SECURE=true`, strong `APEX_SECRET_KEY`
- [ ] HTTPS only; HSTS enabled (the API sends it when cookies are secure)
- [ ] Ports bound to localhost; firewall allows only 443 (and SSH)
- [ ] API docs disabled (automatic in production)
- [ ] Backups scheduled and a restore tested
- [ ] OS and image updates applied; `pip-audit` and `npm audit` run in CI
- [ ] Rule profile verified against official Apex pages before relying on compliance labels
