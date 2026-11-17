# Deployment

## Local (no Docker)

See the README. `scripts/dev.sh` runs migrations, the worker, the API (with reload) and the web dev
server.

## Docker Compose

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste into APEX_SECRET_KEY
docker compose up --build
```

Services: `postgres`, `redis`, `api` (runs `alembic upgrade head` on start), `worker`, `web`.
Ports are bound to 127.0.0.1 only.

> The Compose file and Dockerfiles have been syntax-validated (`docker compose config`) but have not
> yet been built in this development environment, which has no Docker daemon.

## Production checklist

- `APEX_ENVIRONMENT=production` — the API refuses to start without a strong `APEX_SECRET_KEY`,
  an `APEX_BOOTSTRAP_TOKEN`, and `APEX_COOKIE_SECURE=true`. API docs are disabled.
- Terminate TLS in front of web and API (HTTPS + WSS) and set `NEXT_PUBLIC_APEX_WS_URL` to `wss://…`.
- Restrict `APEX_CORS_ORIGINS` to the dashboard origin.
- Schedule `scripts/backup-db.sh` (e.g. daily cron) and test restores.
- Keep dev, test and production databases separate (`apex`, `apex_test`, `apex_e2e` in development).

## Health endpoints

| Endpoint | Auth | Use |
|---|---|---|
| `GET /api/health/live` | none | process liveness |
| `GET /api/health/ready` | none | 200 only when PostgreSQL and Redis are reachable, else 503 |
| `GET /api/system/status` | session | full component report (also pushed over `/api/ws`) |
