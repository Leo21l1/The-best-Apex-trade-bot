/**
 * The trader's declared configuration (from the project brief). This is configuration, not
 * market or account data. Apex rule values live in the API's rule profile.
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
