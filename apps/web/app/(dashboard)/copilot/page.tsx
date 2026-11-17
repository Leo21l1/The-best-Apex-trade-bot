import { PlannedFeature } from "@/features/planned/PlannedFeature";

const CAPABILITIES = [
  "Natural-language questions answered from application data through controlled tools",
  "Separates verified data, statistical estimates, AI interpretation and missing information",
  "Premarket briefings, trade explanations and session summaries",
  "No ability to place or manage orders"
];

const DEPENDS_ON = [
  "APEX_ANTHROPIC_API_KEY configured server-side",
  "Market context tools (Phase 5)"
];

export default function Page() {
  return <PlannedFeature path="/copilot" capabilities={CAPABILITIES} dependsOn={DEPENDS_ON} />;
}
