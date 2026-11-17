import { PlannedFeature } from "@/features/planned/PlannedFeature";

const CAPABILITIES = [
  "A: Opening Range Breakout",
  "B: VWAP Trend Continuation",
  "C: Liquidity Sweep Reversal",
  "D: Breakout and Retest",
  "E: Momentum Pullback",
  "F: Volatility Expansion",
  "G: Mean Reversion",
  "H: Multi-Timeframe Trend Alignment"
];

const DEPENDS_ON = [
  "Strategy engine (Phase 3)",
  "Backtesting validation (Phase 6)"
];

export default function Page() {
  return <PlannedFeature path="/strategies" capabilities={CAPABILITIES} dependsOn={DEPENDS_ON} />;
}
