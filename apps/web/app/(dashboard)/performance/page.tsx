import { PlannedFeature } from "@/features/planned/PlannedFeature";

const CAPABILITIES = [
  "Win rate, profit factor, expectancy, drawdown",
  "Strategy-level attribution",
  "Statistically supported recurring-mistake detection"
];

const DEPENDS_ON = [
  "Trading journal (Phase 6)"
];

export default function Page() {
  return <PlannedFeature path="/performance" capabilities={CAPABILITIES} dependsOn={DEPENDS_ON} />;
}
