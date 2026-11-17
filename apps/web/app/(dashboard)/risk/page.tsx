import { PlannedFeature } from "@/features/planned/PlannedFeature";

const CAPABILITIES = [
  "Position sizing from tick size, tick value, commission and slippage",
  "Estimated dollar risk and loss after costs at the stop",
  "Share of remaining risk budget consumed by a trade",
  "Rule-violation checks against the verified profile \u2014 deterministic, never LLM-generated"
];

const DEPENDS_ON = [
  "Instrument specifications (Phase 3)",
  "Verified Apex rule profile (Phase 4)"
];

export default function Page() {
  return <PlannedFeature path="/risk" capabilities={CAPABILITIES} dependsOn={DEPENDS_ON} />;
}
