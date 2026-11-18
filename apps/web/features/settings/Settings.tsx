"use client";

import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, Panel, StatePill } from "@/components/ui/primitives";
import { IntegrationTests } from "./IntegrationTests";
import { useSession } from "@/hooks/useSession";
import { useSystemStatus } from "@/hooks/useSystemStatus";
import { humanizeComponent } from "@/lib/format";
import { ADVANCED, NAV } from "@/lib/nav";
import { TRADER_PROFILE } from "@/lib/profile";
import Link from "next/link";
import { DataReadinessPanel } from "@/features/system/DataReadiness";
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
            <div className="flex justify-between"><dt className="text-muted">Broker connection</dt><dd className="font-mono">None</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Order submission</dt><dd className="font-mono">Not implemented</dd></div>
          </dl>
          <p className="mt-3 text-xs text-faint">
            PROJECT APEX analyzes markets, monitors the economic calendar and keeps your journal. You place
            every trade yourself on your own platform; nothing here can submit, modify or cancel an order.
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

        <Panel title="Health">
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

        <Panel title="Advanced tools">
          <p className="mb-2 text-xs text-faint">
            Kept for research and diagnostics; not part of the everyday workflow.
          </p>
          <ul className="grid grid-cols-2 gap-1.5 text-sm" data-testid="advanced-tools">
            {ADVANCED.map((href) => {
              const n = NAV.find((x) => x.href === href);
              return n ? (
                <li key={href}><Link href={href} className="text-accent hover:underline">{n.label}</Link></li>
              ) : null;
            })}
          </ul>
        </Panel>
      </div>
      <div className="mt-4">
        <DataReadinessPanel />
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
