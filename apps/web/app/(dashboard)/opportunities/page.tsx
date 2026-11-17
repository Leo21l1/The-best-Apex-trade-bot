import { PlannedFeature } from "@/features/planned/PlannedFeature";

const CAPABILITIES = [
  "Instrument, strategy, direction and setup-quality score (not a probability)",
  "Entry zone, stop, targets, risk/reward and estimated costs",
  "Historical sample size and expectancy once a strategy is validated",
  "Confidence and uncertainty indicators, news exposure, risk eligibility",
  "Zero opportunities is a valid, expected result"
];

const DEPENDS_ON = [
  "Scanner and strategy modules (Phase 3)",
  "Research validation standards (Phase 6)",
  "Risk Guardian (Phase 4)"
];

export default function Page() {
  return <PlannedFeature path="/opportunities" capabilities={CAPABILITIES} dependsOn={DEPENDS_ON} />;
}
