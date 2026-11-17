"use client";

import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, Panel, StatePill } from "@/components/ui/primitives";
import { IntegrationTests } from "./IntegrationTests";
import { useSession } from "@/hooks/useSession";
import { useSystemStatus } from "@/hooks/useSystemStatus";
import { humanizeComponent } from "@/lib/format";
import { TRADER_PROFILE } from "@/lib/profile";
import { ChangePassword, Users } from "./Security";

const INTEGRATIONS = ["market_data", "tradovate_readonly", "ai_copilot", "apex_rule_profile"];

export function Settings() {
  const { user } = useSession();
  const { status } = useSystemStatus();

  return (
    <>
      <PageHeader path="/settings" />
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Execution safety">
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between"><dt className="text-muted">Execution mode</dt><dd className="font-mono">ANALYSIS_ONLY (locked)</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Live order routing</dt><dd className="font-mono">Not implemented</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Paper trading</dt><dd className="font-mono">Isolated simulator</dd></div>
          </dl>
          <p className="mt-3 text-xs text-faint">
            Execution mode cannot be changed from the UI or environment. Automated order entry,
            cancellation, stop/target modification and UI automation are excluded by design and
            by automated tests.
          </p>
        </Panel>

        <Panel title="Account">
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between gap-3"><dt className="text-muted">Signed in as</dt><dd className="truncate">{user?.email}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Role</dt><dd>{user?.role}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Prop firm</dt><dd>{TRADER_PROFILE.propFirm}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Account</dt><dd>{TRADER_PROFILE.accountType}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Platform</dt><dd>{TRADER_PROFILE.platform}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Timezone</dt><dd className="font-mono">{TRADER_PROFILE.timezone}</dd></div>
          </dl>
        </Panel>

        <Panel title="Integrations">
          <ul className="space-y-3">
            {INTEGRATIONS.map((name) => {
              const c = status?.components.find((x) => x.name === name);
              return (
                <li key={name} className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm">{humanizeComponent(name)}</p>
                    <p className="text-xs text-faint">{c?.detail ?? "…"}</p>
                  </div>
                  {c && <StatePill state={c.state} />}
                </li>
              );
            })}
          </ul>
        </Panel>

        <Panel title="Watchlist (configured instruments)">
          <ul className="grid grid-cols-2 gap-2 text-sm">
            {TRADER_PROFILE.instruments.map((i) => (
              <li key={i.symbol} className="rounded border border-line bg-panel-2 px-3 py-1.5 font-mono">
                {i.symbol} · {i.micro}
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-faint">
            Star instruments on the Market Radar to build your watchlist. Styles:{" "}
            {TRADER_PROFILE.styles.join(", ")}.
          </p>
        </Panel>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <ChangePassword />
        {user?.role === "admin" && <IntegrationTests />}
        {user?.role === "admin" && <Users selfEmail={user.email} />}
      </div>
      <div className="mt-4">
        <Callout tone="warn" title="Credentials">
          API keys and broker credentials are configured only as server-side environment
          variables. They are never stored in, or sent to, the browser.
        </Callout>
      </div>
    </>
  );
}
