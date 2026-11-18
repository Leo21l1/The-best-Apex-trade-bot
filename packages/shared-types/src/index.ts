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
  no_losing_trades?: boolean;
  avg_hold_minutes?: number | null;
  max_consecutive_wins?: number;
  max_consecutive_losses?: number;
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
  bars_held?: number;
  session?: string;
  weekday?: string;
  hour_et?: number;
  regime?: string;
  stop_ticks?: number;
  planned_rr?: number | null;
  rvol?: number | null;
}

export interface SegmentRow {
  key: string; trades: number; win_rate: number | null; profit_factor: number | null; expectancy_r: number | null;
  net_profit: number; max_drawdown: number; avg_win: number | null; avg_loss: number | null; expectancy: number | null; small_sample: boolean;
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
  segments?: { in_sample: Record<string, SegmentRow[]>; out_of_sample: Record<string, SegmentRow[]>; note: string };
  execution_policy?: Record<string, string>;
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
  progress?: { phase: string; at: string; bars?: number; first?: string; last?: string } | null;
  started_at?: string | null;
  elapsed_seconds?: number;
  worker_note?: string;
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
  setup?: string | null;
  thesis?: string | null;
  psychology?: string | null;
  emotion_tags?: string[];
  lessons?: string | null;
  tags?: string[];
  chart_links?: string[];
  /** Execution fields changed by hand (the original record is kept in ``original``). */
  corrected_fields?: string[];
  corrections?: Record<string, { value: unknown; at: string; by: string | null; provenance: string; derived: boolean }>;
  original?: Record<string, unknown> | null;
  corrected_at?: string | null;
  record_provenance?: string;
}

export interface JournalNote { id: number; trade_id: number; body: string; created_at: string; updated_at: string | null }
export interface JournalAttachment { id: number; trade_id: number; filename: string; content_type: string; size: number; caption: string | null; created_at: string; url: string }
export interface JournalHistoryItem { id: number; at: string; user: string | null; action: string; field: string | null; old: unknown; new: unknown }
export interface DeletedTrade { edit_id: number; trade_id: number; deleted_at: string; by: string | null; symbol: string; direction: string; net_pnl: number; exit_time: string; attachments_lost: number }

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
  low_sample?: boolean;
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

// ---- Phase 7: users -------------------------------------------------------------------------

export interface ManagedUser {
  id: string;
  email: string;
  role: "admin" | "viewer";
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

// ---- Trading terminal, trade review, replay ----------------------------------------------

export interface TradeLabel {
  label: "BUY" | "SHORT" | "WAIT" | "NO TRADE";
  reasons: string[];
  quality: DataQuality;
  signal: { id: number; strategy_id: string; direction: "long" | "short"; entry: number; stop: number; targets: number[]; score: number; bar_ts: string } | null;
}

export interface ChartEvent {
  id: number;
  t: number;
  ts: string;
  title: string;
  importance: "high" | "medium" | "low";
  category: string;
  affected: string[];
  source: string;
  source_ref: string | null;
  detected_at: string | null;
  bias: string;
  explanation: string;
  affects_symbol: boolean;
  /** "news" for published announcements from official feeds; absent for calendar events. */
  kind?: "news";
  impact?: string;
}

export interface ChartSignal {
  t: number;
  direction: "long" | "short";
  label: "BUY" | "SHORT";
  strategy_id: string;
  status: string;
  entry: number;
  stop: number;
  targets: number[];
  score: number;
  explanation: string;
}

export interface Overlays {
  symbol: string;
  timeframe: Timeframe;
  ema20: { t: number; v: number }[];
  ema50: { t: number; v: number }[];
  vwap: { t: number; v: number }[];
  levels: Record<string, number>;
  signals: ChartSignal[];
  events: ChartEvent[];
  label: TradeLabel;
}

export interface TradePlanOut {
  id: number;
  created_at: string;
  symbol: string;
  direction: "long" | "short";
  entry: number | null;
  stop: number;
  target: number | null;
  quantity: number;
  strategy_id: string | null;
  thesis: string | null;
  signal_snapshot: TradeLabel | null;
  risk_snapshot: (TradeCheck & { reference_price: number }) | null;
  status: "draft" | "executed_paper" | "executed_manual" | "cancelled";
  execution: string | null;
}

export interface LiveStatus {
  live_trading_enabled: false;
  reason: string;
  manual_workflow: string[];
}

export interface CoachFinding {
  code: string;
  title: string;
  evidence: string;
  severity: "high" | "medium" | "low";
  basis: string;
}

export interface NewsAttributionEvent {
  title: string;
  ts: string;
  importance: string;
  category: string;
  phase: "before_entry" | "while_open" | "after_exit";
  source: string;
  source_ref: string | null;
  known_before_entry: boolean;
  reaction: null | { change: number; direction: "up" | "down" | "flat"; range_15m: number; range_ratio_vs_prior_hour: number | null; volatility_increased: boolean };
  direction_aligned: boolean | null;
  description: string;
  forecast: string;
}

export interface Scenario {
  name: string;
  detail: string;
  pnl: number;
  difference_vs_actual: number;
  hypothetical: true;
  available_at_entry: boolean;
}

export interface TradeReview {
  version: string;
  generated_at: string;
  journal: Record<"trade_summary" | "entry_analysis" | "market_conditions" | "news_context" | "risk_analysis" | "execution_review" | "outcome" | "improvement_opportunities" | "lesson", string> & Partial<Record<"price_action_at_entry" | "price_action_after", string>>;
  coaching: {
    did_well: string[];
    could_improve: string[];
    supported_mistakes: CoachFinding[];
    information_at_entry: Record<string, unknown>;
    after_entry: string;
    alternatives: string[];
    next_objective: string;
    outcome_note: string;
  };
  scenarios: Scenario[];
  timeline: { ts: string; kind: "news" | "reaction" | "plan" | "signal" | "entry" | "exit"; text: string }[];
  ai?: { text: string; model: string; generated_at: string; grounding: string };
}

export interface TradeDetail extends JournalTrade {
  /** Set when the automatic review could not be generated; the stored trade is still returned. */
  review_error?: string | null;
  slippage: number;
  mfe: number | null;
  mae: number | null;
  plan_id: number | null;
  duration_seconds: number;
  simulated: boolean;
  broker_record: boolean;
  reviewed: boolean;
  findings: string[];
  context: null | {
    market: Record<string, unknown> & { available: boolean };
    news: { events: NewsAttributionEvent[]; high_impact_near_entry: boolean; note: string };
    signal_at_entry: Record<string, unknown> | null;
    plan: Record<string, unknown> | null;
    r_multiple: number | null;
    duration_seconds: number;
    session_at_entry?: SessionAtEntry;
  };
  review: TradeReview | null;
  reviewed_at: string | null;
}

export interface ReplayData {
  trade_id: number;
  symbol: string;
  provider: string;
  data_available?: boolean;
  /** Why replay is missing or incomplete (null when bars cover the trade). */
  data_note?: string | null;
  bars: Bar[];
  entry: { t: number; price: number; direction: "long" | "short" };
  exit: { t: number; price: number };
  stop: number | null;
  target: number | null;
  net_pnl: number;
  signal_at_entry: Record<string, unknown> | null;
  market_at_entry: Record<string, unknown> | null;
  events: { t: number; known_from: number; title: string; importance: string; affected: string[] }[];
  hindsight: TradeReview["coaching"] | null;
  scenarios: Scenario[] | null;
}

// ---- Smart money concepts / confluence -------------------------------------------------------

export type SmcDirection = "bullish" | "bearish" | "neutral";

export interface SmcPattern {
  id: string;
  type: string;
  direction: SmcDirection;
  formed_at: string;
  confirmed_at: string | null;
  top: number;
  bottom: number;
  status: string;
  invalidation: string;
  invalidation_price: number | null;
  evidence: string[];
  quality: number;
  extra: Record<string, unknown>;
}

export interface LiquidityLevel {
  name: string;
  price: number;
  side: "above" | "below";
  distance: number;
  distance_atr: number;
  touches: number;
  formed_at: string;
  score: number;
  label: string;
  timeframe?: string;
}

export interface SmcDataState {
  available: boolean;
  provider: string;
  quality: string;
  last_bar: string | null;
  age_seconds: number | null;
  stale: boolean;
  price: number | null;
}

export interface SmcAnalysis {
  symbol: string;
  timeframe: string;
  as_of: string | null;
  bars: number;
  atr: number | null;
  trend: SmcDirection;
  patterns: SmcPattern[];
  swings: { time: string; kind: "high" | "low"; price: number; label: string; confirmed: boolean; confirmed_at: string | null }[];
  indicators: Record<string, number | string | null>;
  premium_discount: { range_high: number; range_low: number; equilibrium: number; position: number; zone: string; basis: string } | null;
  liquidity: LiquidityLevel[];
  disclaimer: string;
  data: SmcDataState;
}

export interface ConfluenceFamily { family: "context" | "location" | "trigger"; vote: number; weight: number; evidence: string[] }

export interface ConfluencePlan {
  direction: "long" | "short";
  entry_zone: [number, number];
  entry: number;
  entry_conditions: string[];
  stop: number;
  stop_basis: string;
  targets: { price: number; basis: string }[];
  reward_risk: number | null;
  rr_ok: boolean;
  invalidation: string;
}

export interface LiquidityMap {
  price: number | null;
  levels: LiquidityLevel[];
  nearest_above: LiquidityLevel[];
  nearest_below: LiquidityLevel[];
  ranking: string;
  label: string;
}

export interface Confluence {
  symbol: string;
  outcome: "BUY BIAS" | "SHORT BIAS" | "WAIT" | "NO TRADE";
  headline: string;
  confirmation_required: boolean | null;
  score: number;
  max_score: number;
  families: ConfluenceFamily[];
  gates: string[];
  plan: ConfluencePlan | null;
  news: { status: string; summary: string; events: { title: string; ts: string; importance: string }[] };
  data: SmcDataState;
  risk: { known: boolean; risk_budget: number | null; rules_verified: boolean; stale: boolean } | null;
  timeframes: Record<string, { trend: SmcDirection; as_of: string | null; patterns: number }>;
  validation: string;
  method: string;
  disclaimer: string;
  liquidity: LiquidityMap;
}

export interface SmcScanCard {
  symbol: string;
  outcome: Confluence["outcome"];
  headline: string;
  direction: "long" | "short" | null;
  setups: string[];
  families: ConfluenceFamily[];
  score: number;
  max_score: number;
  entry_zone: [number, number] | null;
  stop: number | null;
  invalidation: string | null;
  targets: { price: number; basis: string }[];
  reward_risk: number | null;
  validation: string;
  news: Confluence["news"];
  risk: Confluence["risk"];
  data: SmcDataState;
  not_ranked_because: string[];
}

export interface SmcScan { as_of: string; ranked: SmcScanCard[]; watch: SmcScanCard[]; note: string }

// ---- Trading session intelligence / readiness checklist ----------------------------------

export type CheckStatus = "PASS" | "FAIL" | "WAIT" | "UNKNOWN";
export interface CheckItem { key: string; name: string; status: CheckStatus; reason: string; evidence?: Record<string, unknown>; calculation?: string }
export interface SessionWindow { key: string; label: string; start: string | null; end: string | null }
export interface SessionMarket { open: boolean; reason: string; early_close: boolean; calendar: string; trading_date?: string; next_open?: string | null }
export interface Checklist {
  symbol: string; as_of: string; ny_time: string; style: string; window: SessionWindow; market: SessionMarket;
  items: CheckItem[]; passed: number; total: number; failures: string[]; unknowns: string[]; critical: string[];
  status: "WAIT" | "REVIEW SETUP" | "NO TRADE"; direction: string | null;
  relative_volume: { available: boolean; ratio?: number | null; current_volume?: number; reference_volume?: number; window_et?: string; reason?: string; acceleration?: number | null };
  relative_volatility: { available: boolean; ratio?: number | null; label?: string; reason?: string };
  regime: { label: string; efficiency_ratio_2h: number | null; vwap_crosses_today: number; definition: string };
  disclaimer: string;
}
export interface Readiness {
  session: { ny_time: string; trading_date: string; window: SessionWindow; next_transition: { label: string; at: string; at_et: string; in_minutes: number }; market: SessionMarket };
  data: { status: string; reason?: string | null; provider?: string; last_bar?: string | null; age_seconds?: number };
  news: { status: string; summary: string };
  risk: { known: boolean; risk_budget: number | null; rules_verified: boolean } | null;
  setup: { strategy_id: string; direction: string; entry: number; stop: number; targets: number[]; detected_at_et: string } | null;
  checklist: Checklist;
  liquidity_note: string;
}

export interface SessionAtEntry {
  entry_et: string;
  exit_et: string;
  window: SessionWindow;
  market: { open: boolean; reason: string; early_close: boolean; calendar: string };
  checklist: {
    status: string; passed: number; total: number; failures: string[]; unknowns: string[]; direction: string | null;
    regime: { label: string; efficiency_ratio_2h: number | null; vwap_crosses_today: number };
    relative_volume: number | null; relative_volatility: string | null;
    items: { key: string; name: string; status: CheckStatus; reason: string }[];
  };
  planned_reward_risk: number | null;
  stop_reached_during_trade: boolean | null;
  target_reached_during_trade: boolean | null;
  bars_before_entry: number;
  note: string;
}

/* --- Live Trade Entry Confirmation ------------------------------------------------------------ */
export type ConfirmationState = "LONG READY" | "SHORT READY" | "WAIT" | "NO TRADE" | "DATA UNAVAILABLE";
export interface ConfirmationCondition {
  key: string;
  name: string;
  status: CheckStatus;
  reason: string;
  evidence: Record<string, unknown>;
  mandatory: boolean;
  confirm?: string;
}
export interface ConfirmationLevels {
  direction: "long" | "short";
  entry_zone: [number, number];
  entry: number;
  entry_with_slippage: number;
  trigger: string;
  stop: number;
  t1: number;
  t2: number | null;
  extended_target: number | null;
  extended_basis: string | null;
  stop_points: number;
  stop_ticks: number;
  loss_per_contract: number | null;
  reward_per_contract_t1: number | null;
  reward_per_contract_t2: number | null;
  rr_t1: number | null;
  rr_t2: number | null;
  point_value: number;
  tick_size: number;
  costs_per_contract: number | null;
  /** Costs when the target (limit order) fills: commission + entry slippage. */
  costs_if_target: number | null;
  gross_rr_t1: number | null;
  gross_rr_t2: number | null;
  /** Configured minimum net R:R to T1 (APEX_MIN_NET_REWARD_RISK). */
  min_net_rr: number;
  invalidation_price: number;
  expires_at: string | null;
  basis: string;
}
export interface EligibleWindow { start: string; start_et: string; end_et: string; date: string; in_minutes?: number; now: boolean }
export interface EntryConfirmation {
  symbol: string;
  contract?: string | null;
  provider?: string;
  state: ConfirmationState;
  summary: string;
  strategy_id: string | null;
  direction: "long" | "short" | null;
  data_class: string;
  data_timestamp: string | null;
  as_of: string;
  ny_time: string;
  conditions: ConfirmationCondition[];
  mandatory: string[];
  passed: number;
  total: number;
  missing: { key: string; name: string; status: CheckStatus; reason: string; would_confirm?: string | null }[];
  levels: ConfirmationLevels | null;
  max_size: number | null;
  apex_compliance: string;
  news_status: string | null;
  next_eligible_window: EligibleWindow | null;
  regime: { labels: string[]; evidence?: Record<string, unknown> } | null;
  /** Last completed 1-minute close the evaluation used. */
  current_price?: number | null;
  /** Pre-registered study configuration matching the live gate (e.g. t1_net15). */
  config?: string | null;
  /** Time of the account snapshot the Risk Guardian used (null = none). */
  account_snapshot_at?: string | null;
  /** True only for LONG READY / SHORT READY. Everything else is informational. */
  actionable?: boolean;
  /** Research-study classification of this strategy for the configuration the live gate uses. */
  evidence_classification?: string;
  evidence_required_for_ready?: boolean;
  expires_at: string | null;
  disclaimer: string;
}

/* --- Data readiness / backfill ---------------------------------------------------------------- */
export interface SymbolCoverage {
  symbol: string;
  provider: string | null;
  status?: string;
  reason?: string;
  target_sessions?: number;
  sessions_stored?: number;
  sessions_expected?: number;
  missing_sessions?: string[];
  earliest_bar?: string | null;
  latest_bar?: string | null;
  bars_total?: number;
  relative_volume_readiness?: string;
  volatility_readiness?: string;
  backtest_readiness?: string;
  rule?: string;
}
export interface BackfillJob {
  id: string;
  symbols: string[];
  target: number;
  status: string;
  approved_cost_usd: number | null;
  created_at: string;
  updated_at?: string;
  finished_at?: string;
  progress: Record<string, { fetched_sessions?: number; bars?: number; remaining?: number; sessions_stored?: number }>;
  errors: string[];
}
export interface DataReadiness {
  live_provider: string | null;
  historical_provider: string | null;
  live_feed: {
    state: string;
    class: string;
    last_error?: string | null;
    provider_last_error?: string | null;
    reconnects?: number | null;
    last_event: Record<string, string>;
    contracts: Record<string, { contract: string; rule: string; since: string }>;
  };
  symbols: SymbolCoverage[];
  backfill_job: BackfillJob | null;
  labels: string;
}
export interface BackfillEstimate {
  provider: string;
  target_sessions: number;
  items: { symbol: string; missing_sessions: string[]; sessions_stored: number | null; estimated_cost_usd: number | null; requests?: number; note: string | null }[];
  estimated_cost_usd: number | null;
  /** True when any cost request failed: no backfill can be started from this estimate. */
  estimate_failed?: boolean;
  errors?: string[];
  note: string;
}
