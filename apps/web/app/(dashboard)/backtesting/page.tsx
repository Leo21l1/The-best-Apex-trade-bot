import { PlannedFeature } from "@/features/planned/PlannedFeature";

const CAPABILITIES = [
  "Event-driven replay with realistic fills, spread, commission, slippage",
  "Walk-forward and out-of-sample testing",
  "Monte Carlo and parameter sensitivity",
  "Look-ahead, leakage and multiple-testing safeguards",
  "Every experiment versioned and reproducible"
];

const DEPENDS_ON = [
  "Historical data store (Phase 2)",
  "Backtest engine (Phase 6)"
];

export default function Page() {
  return <PlannedFeature path="/backtesting" capabilities={CAPABILITIES} dependsOn={DEPENDS_ON} />;
}
