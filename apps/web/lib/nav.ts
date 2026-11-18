export interface NavItem {
  href: string;
  label: string;
  /** Second key of the "g <key>" navigation chord. */
  key: string;
  group: "Markets" | "Account" | "Research" | "System";
  /** Implementation phase that delivers the page's functionality. */
  phase: number;
  summary: string;
}

export const NAV: NavItem[] = [
  { href: "/", label: "Market Analyzer", key: "a", group: "Markets", phase: 15,
    summary: "ES, NQ and MNQ: bias, structure, liquidity, zones and setups explained from closed candles." },
  { href: "/news", label: "News", key: "n", group: "Markets", phase: 15,
    summary: "Eastern-time economic calendar (ForexFactory), re-evaluated every minute, with source freshness." },
  { href: "/overview", label: "Session Overview", key: "o", group: "Markets", phase: 1,
    summary: "Session status, data provenance, risk budget and today's plan." },
  { href: "/radar", label: "Market Radar", key: "r", group: "Markets", phase: 3,
    summary: "Multi-timeframe scanner across the supported futures universe." },
  { href: "/opportunities", label: "Opportunity Leaderboard", key: "l", group: "Markets", phase: 3,
    summary: "Explainable, evidence-ranked setups. May legitimately be empty." },
  { href: "/charts", label: "Advanced Charts", key: "c", group: "Markets", phase: 2,
    summary: "Candles, EMA/VWAP, levels, strategy signals and news markers, labelled with data class." },
  { href: "/smc", label: "Smart Money Analysis", key: "x", group: "Markets", phase: 9,
    summary: "Automatic liquidity sweeps, order blocks, fair value gaps, structure, liquidity map and multi-timeframe confluence." },
  { href: "/terminal", label: "Trading Terminal", key: "t", group: "Markets", phase: 8,
    summary: "Chart, order ticket, risk preview and paper execution. Live Apex orders stay disabled." },
  { href: "/copilot", label: "AI Copilot", key: "i", group: "Markets", phase: 5,
    summary: "Ask questions answered from verified application data via controlled tools." },
  { href: "/account", label: "Apex Account Health", key: "h", group: "Account", phase: 4,
    summary: "EOD drawdown threshold, daily loss allowance, profit target progress." },
  { href: "/risk", label: "Risk Guardian", key: "k", group: "Account", phase: 4,
    summary: "Deterministic position sizing and per-trade rule checks." },
  { href: "/journal", label: "Trading Journal", key: "j", group: "Account", phase: 6,
    summary: "Record the trades you execute elsewhere and review net P&L, R, mistakes and discipline." },
  { href: "/trade", label: "Trade Review & Replay", key: "v", group: "Account", phase: 8,
    summary: "Automatic journal, coaching, news timeline, hypothetical scenarios and candle-by-candle replay." },
  { href: "/performance", label: "My Trading Performance", key: "p", group: "Account", phase: 6,
    summary: "Results by period, strategy, instrument, time of day and news, with sample sizes and rule adherence." },
  { href: "/strategies", label: "Strategy Laboratory", key: "s", group: "Research", phase: 3,
    summary: "Strategy specifications, validation status and eligibility." },
  { href: "/research", label: "Strategy Research", key: "q", group: "Research", phase: 14,
    summary: "Walk-forward study of every strategy on ES/NQ: win-rate and expectancy leaderboards with confidence intervals, stress tests and Apex evaluation replay." },
  { href: "/backtesting", label: "Backtesting", key: "b", group: "Research", phase: 6,
    summary: "Event-driven backtests, walk-forward and Monte Carlo analysis." },
  { href: "/paper", label: "Paper Simulator", key: "m", group: "Research", phase: 6,
    summary: "Raw view of the isolated order simulator (orders, fills, activity)." },
  { href: "/calendar", label: "Economic Calendar", key: "e", group: "Research", phase: 5,
    summary: "CPI, FOMC, NFP and other events with configurable alert windows." },
  { href: "/settings", label: "Settings & Integrations", key: ",", group: "System", phase: 1,
    summary: "Configuration, integrations and execution-safety policy." },
  { href: "/system", label: "System Health", key: "y", group: "System", phase: 1,
    summary: "Live status of API, database, cache, worker and data feeds." },
];

/** Pages whose phase is at or below this are implemented. */
export const COMPLETED_PHASE = 15;

export const NAV_GROUPS = ["Markets", "Account", "Research", "System"] as const;

/** The three primary tabs. Everything else stays reachable by URL but is not in normal navigation. */
export const TABS = ["/", "/news", "/journal"] as const;
export const SETTINGS_HREF = "/settings";
/** Advanced tools listed only on the settings page (execution/simulation pages are not listed). */
export const ADVANCED = ["/charts", "/smc", "/research", "/backtesting", "/strategies", "/calendar", "/performance",
  "/trade", "/overview", "/account", "/risk", "/copilot", "/system"] as const;
/** Pages reachable from the keyboard / command palette. */
export const VISIBLE: NavItem[] = NAV.filter((n) => (TABS as readonly string[]).includes(n.href) || n.href === SETTINGS_HREF || n.href === "/system");

export function navByKey(key: string): NavItem | undefined {
  return VISIBLE.find((n) => n.key === key);
}

export function navByPath(pathname: string): NavItem | undefined {
  return NAV.find((n) => n.href === pathname);
}

export function filterNav(query: string): NavItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return VISIBLE;
  return VISIBLE.filter(
    (n) => n.label.toLowerCase().includes(q) || n.summary.toLowerCase().includes(q),
  );
}
