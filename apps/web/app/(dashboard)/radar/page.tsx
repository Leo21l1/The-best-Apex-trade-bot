import { PlannedFeature } from "@/features/planned/PlannedFeature";

const CAPABILITIES = [
  "Scan 1m\u2013weekly timeframes across ES, MES, NQ, MNQ, YM, MYM, RTY, M2K, CL, MCL, GC, MGC",
  "Trend, momentum, volatility, VWAP, opening range, market structure",
  "Previous-day / overnight levels, liquidity sweeps, fair value gaps",
  "Every signal rule defined mathematically and reproducible",
  "Economic-event risk overlay per instrument"
];

const DEPENDS_ON = [
  "Licensed market data provider (Phase 2)",
  "Instrument specifications (Phase 3)",
  "Scanner engine (Phase 3)"
];

export default function Page() {
  return <PlannedFeature path="/radar" capabilities={CAPABILITIES} dependsOn={DEPENDS_ON} />;
}
