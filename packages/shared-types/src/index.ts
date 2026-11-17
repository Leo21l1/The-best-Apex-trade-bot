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
  risk?: OpportunityRisk;
  news?: NewsExposure;
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

// ---- Phase 4: rules, account, risk ---------------------------------------------------------

export interface Rule {
  key: string;
  label: string;
  kind: "money" | "contracts" | "text";
  value: string;
  required: boolean;
  verified: boolean;
  source_url: string | null;
  suggested_source_url: string | null;
  verified_at: string | null;
  applies_to: string | null;
  version: string | null;
  notes: string | null;
}

export interface RulesResponse {
  profile: string;
  label: string;
  verified: boolean;
  unverified: string[];
  rules: Rule[];
  warning: string | null;
}

export interface AccountPosition {
  symbol: string;
  quantity: number;
  avg_price: number;
  stop: number | null;
}

export interface AccountState {
  as_of: string;
  profile: string;
  rules_verified: boolean;
  known: boolean;
  source: string | null;
  snapshot_at: string | null;
  snapshot_age_seconds: number | null;
  stale: boolean;
  trading_date: string | null;
  balance: number | null;
  unrealized_pnl: number | null;
  equity: number | null;
  highest_eod_balance: number | null;
  drawdown_threshold: number | null;
  drawdown_room: number | null;
  start_of_day_balance: number | null;
  day_pnl: number | null;
  daily_loss_remaining: number | null;
  risk_budget: number | null;
  profit_to_target: number | null;
  target_reached: boolean;
  drawdown_breached: boolean;
  daily_loss_hit: boolean;
  contracts_in_use: number;
  positions: AccountPosition[];
  warnings: string[];
}

export interface RuleSetValues {
  starting_balance: number;
  profit_target: number;
  max_drawdown: number;
  daily_loss_limit: number;
  max_contracts: number;
  drawdown_stop_level: number | null;
  verified: boolean;
  unverified_rules: string[];
}

export interface AccountStateResponse {
  rules: RuleSetValues;
  state: AccountState;
  disclaimer: string;
}

export interface AccountSnapshot {
  id: number;
  as_of: string;
  created_at: string;
  source: "manual" | "csv";
  balance: string;
  unrealized_pnl: string;
  is_eod: boolean;
  trading_date: string;
  positions: { symbol: string; quantity: number; avg_price: string; stop: string | null }[];
  note: string | null;
}

export interface CsvImportResponse {
  dry_run: boolean;
  imported: number;
  rows: [string, string][];
  errors: string[];
  date_column: string | null;
  balance_column: string | null;
}

export interface TradeCheck {
  symbol: string;
  direction: "long" | "short";
  quantity: number;
  entry: number;
  stop: number;
  target: number | null;
  stop_ticks: number;
  dollar_risk: number;
  cost: number;
  loss_after_costs: number;
  reward_risk: number | null;
  max_size: number | null;
  max_size_by_risk: number | null;
  max_size_by_contract_limit: number;
  budget_used_pct: number | null;
  violations: string[];
  warnings: string[];
  verdict: "violation" | "cannot_confirm" | "no_violation_found";
  may_be_called_compliant: boolean;
  summary: string;
}

export interface RiskCheckResponse {
  check: TradeCheck;
  news: NewsExposure;
  instrument: { symbol: string; tick_size: number; tick_value: number; point_value: number; standard_equivalent: number };
  cost_assumptions: { commission_per_side: number; slippage_ticks_per_side: number };
  account: { known: boolean; stale: boolean; risk_budget: number | null; rules_verified: boolean };
  disclaimer: string;
}

export interface OpportunityRisk {
  verdict: "violation" | "cannot_confirm" | "no_violation_found" | "invalid";
  max_size: number | null;
  violations: string[];
  warnings: string[];
  summary: string;
  budget_used_pct: number | null;
}

// ---- Phase 5: copilot, calendar, briefing --------------------------------------------------

export interface CopilotToolCall {
  name: string;
  input: Record<string, unknown>;
  is_error: boolean;
  result: unknown;
}

export interface CopilotAnswer {
  id: number;
  answer: string;
  tool_calls: CopilotToolCall[];
  model: string | null;
  stop_reason: string | null;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  fallback_used: boolean;
  note: string | null;
}

export interface CopilotHistoryItem {
  id: number;
  created_at: string;
  question: string;
  answer: string | null;
  tool_calls: CopilotToolCall[];
  model: string | null;
  stop_reason: string | null;
  error: string | null;
}

export interface CopilotStatus {
  configured: boolean;
  model: string;
  effort: string;
  can_place_orders: false;
  data_sent: string;
}

export interface EconomicEvent {
  id: number;
  ts: string;
  title: string;
  category: string;
  importance: "high" | "medium" | "low";
  affected: string[];
  country: string;
  source: "ics_file" | "ics_url" | "manual";
  source_ref: string | null;
  minutes_until: number;
  in_window: boolean;
  window: { pre_minutes: number; post_minutes: number };
}

export interface CalendarResponse {
  as_of: string;
  window: { pre_minutes: number; post_minutes: number };
  apex_restrictions: string;
  empty_calendar: boolean;
  events: EconomicEvent[];
  note: string;
}

export interface IcsImportResponse {
  dry_run: boolean;
  parsed: number;
  written: number;
  errors: string[];
  preview: { ts: string; title: string; category: string; importance: string; affected: string[] }[];
}

export interface NewsExposure {
  status: "unknown" | "clear" | "inside_window";
  summary: string;
  events: { title: string; ts: string; importance: string }[];
}

export interface BriefingResponse {
  as_of: string;
  phase: "premarket" | "live" | "postmarket" | "closed";
  data: { provider: string | null; quality: DataQuality; scan_as_of: string | null };
  checklist: { status: "ok" | "warning" | "critical"; text: string }[];
  markets: {
    symbol: string;
    last: number | null;
    regime: string;
    vs_vwap: string | null;
    prior_day_high: number | null;
    prior_day_low: number | null;
    prior_day_close: number | null;
    overnight_high: number | null;
    overnight_low: number | null;
    opening_range_high: number | null;
    opening_range_low: number | null;
  }[];
  events_today: EconomicEvent[];
  account: { known: boolean; stale: boolean; risk_budget: number | null; drawdown_room: number | null; daily_loss_remaining: number | null };
  active_candidates: number;
  ranked_opportunities: number;
  note: string;
}

// ---- Phase 6: backtesting, paper trading, journal, analytics ------------------------------

export interface BacktestMetrics {
  trades: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  expectancy_r: number | null;
  t_stat_r: number | null;
  net_profit: number;
  total_commission: number;
  max_drawdown: number;
  max_drawdown_r: number;
  trades_per_day: number | null;
  exposure: number | null;
  daily_sharpe_annualized: number | null;
  exit_reasons: Record<string, number>;
}

export interface BacktestTrade {
  direction: "long" | "short";
  signal_ts: string;
  entry_ts: string;
  exit_ts: string;
  entry: number;
  stop: number;
  target: number;
  exit: number;
  exit_reason: string;
  quantity: number;
  pnl: number;
  commission: number;
  r: number;
  trading_day: string;
}

export interface BacktestResult {
  engine_version: string;
  params: Record<string, unknown>;
  dataset: { bars: number; first: string | null; last: string | null; sha256: string; quality: DataQuality; trading_days: number };
  metrics: BacktestMetrics;
  in_sample: BacktestMetrics;
  out_of_sample: BacktestMetrics;
  split: { in_sample_days: number; out_of_sample_days: number; first_oos_day: string | null };
  walk_forward: { fold: number; first_day: string; last_day: string; metrics: BacktestMetrics }[];
  monte_carlo: null | {
    runs: number;
    seed: number;
    net_profit: { p5: number; p50: number; p95: number };
    max_drawdown: { p50: number; p95: number; p99: number };
    probability_of_loss: number;
  };
  sensitivity: { variant: string; trades: number; win_rate: number | null; profit_factor: number | null; expectancy_r: number | null; net_profit: number; max_drawdown: number }[];
  multiple_testing: { configurations_tested: number; t_stat_r: number | null; z_required_bonferroni: number; significant_after_adjustment: boolean; note: string };
  validation: { passed: boolean; checks: { name: string; passed: boolean; detail: string }[] };
  assumptions: string[];
  trades: BacktestTrade[];
  result_sha256: string;
}

export interface BacktestRun {
  id: number;
  created_at: string;
  status: "queued" | "running" | "done" | "failed";
  strategy_id: string;
  symbol: string;
  provider: string;
  start: string;
  end: string;
  params: Record<string, unknown>;
  validated: boolean | null;
  error: string | null;
  finished_at: string | null;
  summary?: Pick<BacktestMetrics, "trades" | "win_rate" | "profit_factor" | "expectancy_r" | "net_profit" | "max_drawdown">;
  dataset_quality?: DataQuality;
  result?: BacktestResult;
}

export interface PaperOrder {
  id: number;
  symbol: string;
  side: "buy" | "sell";
  type: "market" | "limit" | "stop";
  qty: number;
  filled_qty: number;
  price: number | null;
  status: "working" | "pending" | "partially_filled" | "filled" | "cancelled" | "rejected";
  reason: string | null;
  created_at: string;
  parent_id: number | null;
  role: "entry" | "stop_loss" | "take_profit";
  avg_fill_price: number | null;
}

export interface PaperState {
  id: number;
  provider: string;
  isolated: true;
  notice: string;
  summary: {
    status: "active" | "halted_daily_loss" | "breached";
    balance: number;
    unrealized: number;
    equity: number;
    drawdown_threshold: number;
    drawdown_room: number;
    day_pnl: number;
    daily_loss_remaining: number;
    contracts_in_use: number;
    max_contracts: number;
  };
  positions: { symbol: string; qty: number; avg_price: number; opened_at: string | null; last: number | null }[];
  orders: PaperOrder[];
  fills: { order_id: number | null; ts: string; symbol: string; side: string; qty: number; price: number; commission: number }[];
  closed: { id: number; symbol: string; direction: string; qty: number; entry_time: string; exit_time: string; entry_price: number; exit_price: number; gross_pnl: number; fees: number }[];
  events: { ts: string; text: string }[];
  config: Record<string, number>;
}

export interface JournalTrade {
  id: number;
  source: "csv" | "manual" | "paper";
  trading_date: string;
  symbol: string;
  contract: string | null;
  direction: "long" | "short";
  quantity: number;
  entry_time: string;
  exit_time: string;
  entry_price: number;
  exit_price: number;
  gross_pnl: number;
  fees: number;
  net_pnl: number;
  strategy_id: string | null;
  planned_stop: number | null;
  planned_target: number | null;
  followed_plan: boolean | null;
  mistakes: string[];
  notes: string | null;
}

export interface TradeStats {
  trades: number;
  net_pnl: number;
  fees: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  max_drawdown: number;
  contracts: number;
}

export interface JournalResponse {
  trades: JournalTrade[];
  stats: TradeStats;
  mistake_tags: string[];
}

export interface AnalyticsResponse {
  window_days: number;
  period: "day" | "week" | "month";
  overall: TradeStats;
  by_period: (TradeStats & { key: string })[];
  by_strategy: (TradeStats & { key: string })[];
  by_symbol: (TradeStats & { key: string })[];
  by_hour_et: (TradeStats & { key: string })[];
  by_direction: (TradeStats & { key: string })[];
  mistakes: { tag: string; count: number; share: number; total_pnl: number; avg_pnl_with: number | null; avg_pnl_without: number | null; welch: { t: number | null; p: number | null; note: string | null }; evidence: string }[];
  planned_vs_actual: { trades_with_plan: number; trades_without_plan: number; avg_r: number | null; exits_beyond_stop: number; reached_target: number; followed_plan_rate: number | null };
  rule_violations: { trade_id: number; date: string; rule: string; detail: string }[];
  rules_verified: boolean;
  notes: string[];
}
