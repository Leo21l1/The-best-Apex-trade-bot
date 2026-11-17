# Monitoring

Phase 1 provides:

- **Liveness/readiness probes**: `/api/health/live`, `/api/health/ready` (used by the Docker
  healthcheck and suitable for any orchestrator).
- **Component status**: `/api/system/status` and the `system.status` WebSocket topic: database,
  Redis, worker heartbeat (stale after `APEX_WORKER_HEARTBEAT_STALE_SECONDS`), plus integrations.
- **Structured logs**: one JSON object per line on stdout with `ts`, `level`, `logger`, `msg`,
  `request_id`, and request method/path/status/duration. Ship them with any log collector.

Planned: data-feed freshness and gap metrics (Phase 2), alert-delivery metrics (Phase 3).
