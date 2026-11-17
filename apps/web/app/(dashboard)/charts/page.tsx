import { PlannedFeature } from "@/features/planned/PlannedFeature";

const CAPABILITIES = [
  "Candlestick charts with provider and timestamp on every price",
  "VWAP, opening range, prior-day and overnight levels",
  "Signal annotations with explanations",
  "Live / delayed / historical / simulated labelling"
];

const DEPENDS_ON = [
  "Licensed market data provider (Phase 2)",
  "Historical candle storage (Phase 2)"
];

export default function Page() {
  return <PlannedFeature path="/charts" capabilities={CAPABILITIES} dependsOn={DEPENDS_ON} />;
}
