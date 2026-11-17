/**
 * Types shared between the PROJECT APEX web client and API.
 * They mirror the Pydantic schemas in apps/api/app/schemas. Keep both in sync
 * (contract tests in apps/web/lib/__tests__ exercise the shapes).
 */

export type ExecutionMode = "ANALYSIS_ONLY";

export type ComponentState = "ok" | "degraded" | "down" | "not_configured";

export interface ComponentStatus {
  name: string;
  state: ComponentState;
  detail: string;
  latency_ms: number | null;
  checked_at: string;
}

export interface SystemStatus {
  app: string;
  version: string;
  environment: "development" | "test" | "production";
  execution_mode: ExecutionMode;
  live_order_routing: false;
  timezone: string;
  server_time: string;
  overall: ComponentState;
  components: ComponentStatus[];
}

export interface User {
  id: string;
  email: string;
  role: "admin" | "viewer";
  created_at: string;
  last_login_at: string | null;
}

export interface SessionOut {
  user: User;
  expires_at: string;
}

export interface BootstrapStatus {
  needs_bootstrap: boolean;
  bootstrap_token_required: boolean;
}

/** Provenance of any displayed market value. Prices must never be shown without it. */
export type DataQuality = "live" | "delayed" | "historical" | "simulated" | "unavailable";

export interface WsFrame<T = unknown> {
  topic: string;
  seq: number;
  ts: string;
  data: T;
}
