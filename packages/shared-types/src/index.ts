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
  market_data_quality: DataQuality;
  market_data_provider: string | null;
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

export type Timeframe = "1m" | "3m" | "5m" | "15m" | "30m" | "1h" | "4h" | "1d" | "1w";

export interface Instrument {
  symbol: string;
  name: string;
  exchange: string;
  asset_class: string;
  tick_size: number;
  tick_value: number;
  point_value: number;
  is_micro: boolean;
  sibling: string;
}

/** OHLCV bar; `t` is the bar open time in epoch seconds (UTC). */
export interface Bar {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface Provenance {
  provider: string | null;
  quality: DataQuality;
  as_of: string;
  last_bar: string | null;
  stale: boolean;
  note: string | null;
}

export interface CandlesResponse {
  symbol: string;
  timeframe: Timeframe;
  provenance: Provenance;
  bars: Bar[];
}

export interface SnapshotItem {
  symbol: string;
  last: number | null;
  last_bar: string | null;
  age_seconds: number | null;
  stale: boolean;
}

export interface SnapshotResponse {
  provenance: Provenance;
  items: SnapshotItem[];
}

export interface SymbolFreshness {
  symbol: string;
  last_bar: string | null;
  age_seconds: number | null;
  stale: boolean;
}

export interface FeedStatus {
  configured_provider: string;
  reporting: boolean;
  state: string;
  quality: DataQuality;
  verified: boolean | null;
  description: string | null;
  market_open: boolean;
  reconnects: number;
  duplicates_dropped: number;
  invalid_dropped: number;
  bars_stored: number;
  last_error: string | null;
  stale_after_seconds: number;
  symbols: SymbolFreshness[];
  as_of: string;
}

export interface GapReport {
  symbol: string;
  provider: string | null;
  window_start: string;
  window_end: string;
  expected_bars: number;
  present_bars: number;
  missing_bars: number;
  gaps: { start: string; end: string; missing_bars: number }[];
  note: string;
}

/** 1-minute bar update pushed on WebSocket topic `candles:<SYMBOL>`. */
export interface LiveCandle extends Bar {
  symbol: string;
  timeframe: "1m";
  closed: boolean;
  provider: string;
  quality: DataQuality;
}

// ---- Phase 3: scanner, opportunities, strategies, alerts ---------------------------------

export interface Condition {
  name: string;
  passed: boolean;
  detail: string;
}

export interface RadarTimeframe {
  timeframe: Timeframe;
  bars: number;
  trend: "up" | "down" | "flat" | null;
  rsi: number | null;
  atr_pct: number | null;
  rel_volume: number | null;
  close: number | null;
  note: string | null;
}

export interface StrategyCheck {
  strategy_id: string;
  triggered: boolean;
  side: "long" | "short" | null;
  note: string | null;
  passed: number;
  total: number;
  conditions: Condition[];
}

export interface Levels {
  prior_day_high: number | null;
  prior_day_low: number | null;
  prior_day_close: number | null;
  overnight_high: number | null;
  overnight_low: number | null;
  opening_range_high: number | null;
  opening_range_low: number | null;
  opening_range_complete: boolean;
}

export interface RadarSymbol {
  symbol: string;
  last: number | null;
  last_bar: string | null;
  regime: "trend_up" | "trend_down" | "range" | "mixed" | "unknown";
  regime_definition: string;
  session: "regular" | "overnight" | "closed";
  vwap: number | null;
  vs_vwap: "above" | "below" | null;
  levels: Levels;
  timeframes: RadarTimeframe[];
  evaluations: StrategyCheck[];
}

export interface RadarResponse {
  as_of: string;
  provider: string | null;
  quality: DataQuality;
  market_open: boolean | null;
  symbols: RadarSymbol[];
  note: string | null;
}

export interface ScorePart {
  name: string;
  points: number;
  max: number;
  detail: string;
}

export interface Opportunity {
  id: number;
  status: "active" | "invalidated" | "target_reached" | "expired";
  created_at: string;
  strategy_id: string;
  symbol: string;
  direction: "long" | "short";
  bar_ts: string;
  data_ts: string;
  provider: string;
  quality: DataQuality;
  entry: number;
  entry_low: number;
  entry_high: number;
  stop: number;
  targets: number[];
  reward_risk: number;
  risk_per_contract: number;
  estimated_cost_per_contract: number;
  loss_at_stop_after_costs: number;
  score: number;
  score_breakdown: ScorePart[];
  conditions: Condition[];
  validation: { validated: boolean; reason: string; sample_size: number | null; expectancy_r: number | null };
  news_exposure: string;
  risk_eligibility: string;
  uncertainty: string[];
  rank_eligible: boolean;
  rank_blockers: string[];
}

export interface OpportunitiesResponse {
  as_of: string;
  provider: string | null;
  quality: DataQuality;
  ranked: Opportunity[];
  candidates: Opportunity[];
  score_note: string;
  ranking_rule: string;
}

export interface StrategySpec {
  id: string;
  code: string;
  name: string;
  timeframe: string;
  required_data: string;
  eligible_instruments: string[];
  regime: string;
  entry: string;
  invalidation: string;
  stop: string;
  targets: string;
  time_exit: string;
  testing: string;
  costs: string;
  validation: { validated: boolean; reason: string; sample_size: number | null };
}

export interface StrategiesResponse {
  strategies: StrategySpec[];
  validation_standards: Record<string, number | boolean>;
}

export interface Alert {
  id: number;
  created_at: string;
  kind: string;
  severity: "info" | "warning" | "critical";
  title: string;
  body: string;
  symbol: string | null;
  read: boolean;
}

export interface AlertsResponse {
  unread: number;
  alerts: Alert[];
}
