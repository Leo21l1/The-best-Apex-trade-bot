"use client";

import type { AccountStateResponse, RulesResponse, SnapshotResponse } from "@apex/shared-types";
import Link from "next/link";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, EmptyState, Panel, QualityBadge, Stat, StatePill } from "@/components/ui/primitives";
import { CollapsiblePanel } from "@/components/ui/viz";
import { usePolling } from "@/hooks/usePolling";
import { useSystemStatus } from "@/hooks/useSystemStatus";
import { formatPrice } from "@/lib/candles";
import { formatEt, humanizeComponent } from "@/lib/format";
import { COMPLETED_PHASE, NAV } from "@/lib/nav";
import { money } from "@/lib/money";
import { Briefing } from "./Briefing";
import { TRADER_PROFILE } from "@/lib/profile";

const TICKS: Record<string, number> = { ES: 0.25, NQ: 0.25, YM: 1, RTY: 0.1, CL: 0.01, GC: 0.1 };

export function Overview() {
  const { status } = useSystemStatus();
  const { data: snap } = usePolling<SnapshotResponse>("/api/market/snapshot", 10_000);
  const quality = snap?.provenance.quality ?? "unavailable";
  const { data: acct } = usePolling<AccountStateResponse>("/api/account/state", 20_000);
  const { data: rules } = usePolling<RulesResponse>("/api/rules", 60_000);
  return (
    <>
      <PageHeader path="/" />

      <div className="space-y-3">
        <Callout tone="info" title="Execution mode: ANALYSIS ONLY">
          PROJECT APEX never places, cancels or modifies orders. You place and manage every order
          manually in your authorised Tradovate interface.
        </Callout>
        {rules && !rules.verified && (
          <Callout tone="warn" title="Apex rule profile is UNVERIFIED">
            {rules.unverified.length} required rule(s) have not been checked against official Apex documentation.
            Until they are, no trade will be labelled compliant.{" "}
            <Link href="/account" className="underline">Verify rules</Link>
          </Callout>
        )}
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Briefing />
        <Panel
          title="Market data"
          className="lg:col-span-2"
          actions={<QualityBadge quality={quality} />}
        >
          <p className="mb-3 text-sm text-muted" data-testid="market-provenance">
            {quality === "unavailable"
              ? (snap?.provenance.note ?? "No market data provider configured.")
              : `Provider: ${snap?.provenance.provider} · as of ${formatEt(snap!.provenance.as_of)}`}
          </p>
          {quality === "simulated" && (
            <p className="mb-3 text-xs text-warn">
              SIMULATED — synthetic prices for development, not real market data.
            </p>
          )}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {TRADER_PROFILE.instruments.map((i) => {
              const item = snap?.items.find((x) => x.symbol === i.symbol);
              const show = quality !== "unavailable" && item?.last != null;
              return (
                <Link
                  key={i.symbol}
                  href="/charts"
                  className="rounded-md border border-line bg-panel-2 px-3 py-2 hover:border-faint"
                >
                  <p className="font-mono text-sm">
                    {i.symbol} <span className="text-faint">/ {i.micro}</span>
                  </p>
                  <p className="truncate text-xs text-faint">{i.name}</p>
                  {show ? (
                    <>
                      <p className="mt-1 font-mono text-sm" data-testid={`last-${i.symbol}`}>
                        {formatPrice(item.last!, TICKS[i.symbol] ?? 0.01)}
                      </p>
                      <p className={item.stale ? "text-[11px] text-warn" : "text-[11px] text-faint"}>
                        {item.stale ? "STALE · " : ""}
                        bar {item.last_bar ? formatEt(item.last_bar) : "—"}
                      </p>
                    </>
                  ) : (
                    <p className="mt-1 font-mono text-sm text-faint" aria-label="price unavailable">
                      —
                    </p>
                  )}
                </Link>
              );
            })}
          </div>
          <p className="mt-3 text-xs text-faint">
            Values are the latest stored 1-minute close. Every price shows its provider,
            timestamp and data class; prices are never fabricated.
          </p>
        </Panel>

        <CollapsiblePanel id="ov-risk" title="Risk budget">
          {!acct?.state.known ? (
            <EmptyState title="Unknown">
              No account snapshot has been entered. Risk budget is treated as unknown — not as available.{" "}
              <Link href="/account" className="text-accent hover:underline">Enter balance</Link>
            </EmptyState>
          ) : (
            <div data-testid="overview-risk">
              <p className={acct.state.stale ? "font-mono text-2xl text-warn" : "font-mono text-2xl"}>
                {acct.state.stale ? "Unknown (stale)" : money(acct.state.risk_budget)}
              </p>
              <dl className="mt-3 space-y-1.5 text-sm">
                <div className="flex justify-between"><dt className="text-muted">Drawdown room</dt><dd className="font-mono">{money(acct.state.drawdown_room)}</dd></div>
                <div className="flex justify-between"><dt className="text-muted">Daily loss remaining</dt><dd className="font-mono">{money(acct.state.daily_loss_remaining)}</dd></div>
                <div className="flex justify-between"><dt className="text-muted">Equity</dt><dd className="font-mono">{money(acct.state.equity)}</dd></div>
              </dl>
              <p className="mt-2 text-xs text-faint">From the snapshot you entered; advisory.</p>
            </div>
          )}
        </CollapsiblePanel>

        <CollapsiblePanel id="ov-rules" title="Apex rule values" actions={<StatePill state={rules?.verified ? "ok" : "degraded"} label={rules?.verified ? "Verified" : "Unverified"} />}>
          <dl className="space-y-2 text-sm">
            {(rules?.rules ?? []).filter((r) => r.kind !== "text" && r.value).map((r) => (
              <div key={r.key} className="flex justify-between gap-3">
                <dt className="text-muted">{r.label.replace(" (1 micro = 0.1)", "")}</dt>
                <dd className="font-mono">{r.kind === "money" ? money(Number(r.value)) : `${r.value} contracts`}</dd>
              </div>
            ))}
          </dl>
          <Link href="/account" className="mt-3 inline-block text-xs text-accent hover:underline">Review and verify rules →</Link>
        </CollapsiblePanel>

        <CollapsiblePanel id="ov-system" title="System" className="lg:col-span-2">
          {status ? (
            <>
              <div className="mb-3 grid grid-cols-2 gap-4 sm:grid-cols-3">
                <Stat label="Infrastructure" value={<StatePill state={status.overall} />} hint="Core services only" />
                <Stat label="Server time" value={formatEt(status.server_time)} />
                <Stat label="Environment" value={status.environment} />
              </div>
              <ul className="grid gap-1.5 sm:grid-cols-2">
                {status.components.map((c) => (
                  <li key={c.name} className="flex items-center justify-between gap-2 text-sm">
                    <span className="truncate text-muted">{humanizeComponent(c.name)}</span>
                    <StatePill state={c.state} />
                  </li>
                ))}
              </ul>
              <Link href="/system" className="mt-3 inline-block text-xs text-accent hover:underline">
                Open System Health →
              </Link>
            </>
          ) : (
            <p className="text-sm text-faint">Loading status…</p>
          )}
        </CollapsiblePanel>

        <CollapsiblePanel id="ov-opps" title="Opportunities" className="lg:col-span-3">
          <EmptyState title="No ranked opportunities">
            Nothing is ranked until a strategy passes research validation on real data. Unranked
            research candidates are listed on the{" "}
            <Link href="/opportunities" className="text-accent hover:underline">Opportunity Leaderboard</Link>.
            The system never manufactures a trade to fill this panel.
          </EmptyState>
        </CollapsiblePanel>

        <CollapsiblePanel id="ov-roadmap" title="Build roadmap" className="lg:col-span-3">
          <ul className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2 lg:grid-cols-3">
            {NAV.map((n) => (
              <li key={n.href} className="flex items-center justify-between gap-2">
                <Link href={n.href} className="text-muted hover:text-text">
                  {n.label}
                </Link>
                <span className={n.phase <= COMPLETED_PHASE ? "text-xs text-up" : "text-xs text-faint"}>
                  {n.phase <= COMPLETED_PHASE ? "Available" : `Phase ${n.phase}`}
                </span>
              </li>
            ))}
          </ul>
        </CollapsiblePanel>
      </div>
    </>
  );
}
