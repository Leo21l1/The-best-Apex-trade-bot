import { PlannedFeature } from "@/features/planned/PlannedFeature";

const CAPABILITIES = [
  "EOD drawdown threshold (distinct from intraday trailing drawdown)",
  "Daily loss allowance and profit-target progress",
  "Account snapshots from manual entry or CSV import",
  "Staleness warnings when account data is old or unknown"
];

const DEPENDS_ON = [
  "Verified Apex rule profile (Phase 4)",
  "Account snapshot store (Phase 4)"
];

export default function Page() {
  return <PlannedFeature path="/account" capabilities={CAPABILITIES} dependsOn={DEPENDS_ON} />;
}
