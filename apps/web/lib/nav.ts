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
  { href: "/", label: "Overview", key: "o", group: "Markets", phase: 1,
    summary: "Session status, data provenance, risk budget and today's plan." },
  { href: "/radar", label: "Market Radar", key: "r", group: "Markets", phase: 3,
    summary: "Multi-timeframe scanner across the supported futures universe." },
  { href: "/opportunities", label: "Opportunity Leaderboard", key: "l", group: "Markets", phase: 3,
    summary: "Explainable, evidence-ranked setups. May legitimately be empty." },
  { href: "/charts", label: "Advanced Charts", key: "c", group: "Markets", phase: 2,
    summary: "Candles and volume on nine timeframes, each labelled with provider and data class." },
  { href: "/copilot", label: "AI Copilot", key: "i", group: "Markets", phase: 5,
    summary: "Ask questions answered from verified application data via controlled tools." },
  { href: "/account", label: "Apex Account Health", key: "a", group: "Account", phase: 4,
    summary: "EOD drawdown threshold, daily loss allowance, profit target progress." },
  { href: "/risk", label: "Risk Guardian", key: "k", group: "Account", phase: 4,
    summary: "Deterministic position sizing and per-trade rule checks." },
  { href: "/journal", label: "Trading Journal", key: "j", group: "Account", phase: 6,
    summary: "Imported trades, planned vs actual execution, rule adherence." },
  { href: "/performance", label: "Performance Analytics", key: "p", group: "Account", phase: 6,
    summary: "Daily, weekly and monthly statistics with recurring-mistake detection." },
  { href: "/strategies", label: "Strategy Laboratory", key: "s", group: "Research", phase: 3,
    summary: "Strategy specifications, validation status and eligibility." },
  { href: "/backtesting", label: "Backtesting", key: "b", group: "Research", phase: 6,
    summary: "Event-driven backtests, walk-forward and Monte Carlo analysis." },
  { href: "/calendar", label: "Economic Calendar", key: "e", group: "Research", phase: 5,
    summary: "CPI, FOMC, NFP and other events with configurable alert windows." },
  { href: "/settings", label: "Settings & Integrations", key: ",", group: "System", phase: 1,
    summary: "Configuration, integrations and execution-safety policy." },
  { href: "/system", label: "System Health", key: "y", group: "System", phase: 1,
    summary: "Live status of API, database, cache, worker and data feeds." },
];

/** Pages whose phase is at or below this are implemented. */
export const COMPLETED_PHASE = 2;

export const NAV_GROUPS = ["Markets", "Account", "Research", "System"] as const;

export function navByKey(key: string): NavItem | undefined {
  return NAV.find((n) => n.key === key);
}

export function navByPath(pathname: string): NavItem | undefined {
  return NAV.find((n) => n.href === pathname);
}

export function filterNav(query: string): NavItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return NAV;
  return NAV.filter(
    (n) => n.label.toLowerCase().includes(q) || n.summary.toLowerCase().includes(q),
  );
}
