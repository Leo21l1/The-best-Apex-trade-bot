import { PlannedFeature } from "@/features/planned/PlannedFeature";

const CAPABILITIES = [
  "Trade CSV import from Tradovate exports",
  "Planned vs actual execution",
  "Fees, slippage, net P&L and rule adherence",
  "Daily / weekly / monthly reports"
];

const DEPENDS_ON = [
  "Trade import pipeline (Phase 6)"
];

export default function Page() {
  return <PlannedFeature path="/journal" capabilities={CAPABILITIES} dependsOn={DEPENDS_ON} />;
}
