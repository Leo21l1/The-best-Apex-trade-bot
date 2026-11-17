import { PlannedFeature } from "@/features/planned/PlannedFeature";

const CAPABILITIES = [
  "CPI, FOMC, NFP, GDP, rate decisions, major earnings",
  "Affected markets and configurable alert windows",
  "Applicable Apex news restrictions once verified"
];

const DEPENDS_ON = [
  "Authorised economic calendar provider (Phase 5)"
];

export default function Page() {
  return <PlannedFeature path="/calendar" capabilities={CAPABILITIES} dependsOn={DEPENDS_ON} />;
}
