/**
 * The trader's declared configuration (from the project brief). This is configuration, not
 * market or account data. Apex values are PROVISIONAL REFERENCE VALUES and remain unverified
 * until Phase 4 checks them against official Apex documentation.
 */
export const TRADER_PROFILE = {
  propFirm: "Apex Trader Funding",
  platform: "Tradovate",
  accountType: "$50K End-of-Day (EOD) Evaluation",
  timezone: "America/New_York",
  executionMode: "ANALYSIS_ONLY",
  instruments: [
    { symbol: "ES", name: "E-mini S&P 500", micro: "MES" },
    { symbol: "NQ", name: "E-mini Nasdaq-100", micro: "MNQ" },
    { symbol: "YM", name: "E-mini Dow", micro: "MYM" },
    { symbol: "RTY", name: "E-mini Russell 2000", micro: "M2K" },
    { symbol: "CL", name: "Crude Oil", micro: "MCL" },
    { symbol: "GC", name: "Gold", micro: "MGC" },
  ],
  styles: [
    "Scalping",
    "Intraday momentum",
    "Opening range breakouts",
    "Liquidity sweep reversals",
    "Trend continuation",
    "High-quality mean reversion",
  ],
} as const;

export const PROVISIONAL_APEX_RULES = {
  verified: false,
  values: [
    { label: "Starting balance", value: 50_000 },
    { label: "Evaluation profit target", value: 3_000 },
    { label: "Maximum EOD drawdown", value: 2_000 },
    { label: "Daily loss limit", value: 1_000 },
    { label: "Maximum standard contracts", value: 6, unit: "contracts" as const },
  ],
} as const;
