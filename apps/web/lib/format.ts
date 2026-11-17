import type { ComponentState, DataQuality } from "@apex/shared-types";

export const MARKET_TZ = "America/New_York";

/** Format a timestamp in exchange (New York) time, e.g. "09:30:05 ET". */
export function formatEt(iso: string | Date, withDate = false): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  if (Number.isNaN(d.getTime())) return "—";
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: MARKET_TZ,
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    ...(withDate ? { year: "numeric", month: "short", day: "2-digit" } : {}),
  });
  return `${fmt.format(d)} ET`;
}

/** Age of a timestamp in whole seconds, never negative. */
export function ageSeconds(iso: string, now: number = Date.now()): number {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return Number.POSITIVE_INFINITY;
  return Math.max(0, Math.floor((now - t) / 1000));
}

export function formatAge(seconds: number): string {
  if (!Number.isFinite(seconds)) return "unknown";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

export const STATE_LABEL: Record<ComponentState, string> = {
  ok: "Operational",
  degraded: "Degraded",
  down: "Down",
  not_configured: "Not configured",
};

export const QUALITY_LABEL: Record<DataQuality, string> = {
  live: "LIVE",
  delayed: "DELAYED",
  historical: "HISTORICAL",
  simulated: "SIMULATED",
  unavailable: "NO DATA",
};

/** US-dollar formatting with explicit sign for P&L contexts. */
export function formatUsd(value: number, signed = false): string {
  const s = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Math.abs(value));
  if (!signed) return value < 0 ? `-${s}` : s;
  if (value > 0) return `+${s}`;
  if (value < 0) return `-${s}`;
  return s;
}

export function humanizeComponent(name: string): string {
  const map: Record<string, string> = {
    database: "Database (PostgreSQL)",
    redis: "Cache / PubSub (Redis)",
    worker: "Background worker",
    market_data: "Market data feed",
    apex_rule_profile: "Apex rule profile",
    tradovate_readonly: "Tradovate (read-only)",
    ai_copilot: "AI copilot (Claude)",
  };
  return map[name] ?? name;
}
