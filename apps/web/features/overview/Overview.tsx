"use client";

import type { SnapshotResponse } from "@apex/shared-types";
import Link from "next/link";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, EmptyState, Panel, QualityBadge, Stat, StatePill } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { useSystemStatus } from "@/hooks/useSystemStatus";
import { formatPrice } from "@/lib/candles";
import { formatEt, formatUsd, humanizeComponent } from "@/lib/format";
import { COMPLETED_PHASE, NAV } from "@/lib/nav";
import { PROVISIONAL_APEX_RULES, TRADER_PROFILE } from "@/lib/profile";

const TICKS: Record<string, number> = { ES: 0.25, NQ: 0.25, YM: 1, RTY: 0.1, CL: 0.01, GC: 0.1 };

export function Overview() {
  const { status } = useSystemStatus();
  const { data: snap } = usePolling<SnapshotResponse>("/api/market/snapshot", 10_000);
  const quality = snap?.provenance.quality ?? "unavailable";
  return (
    <>
      <PageHeader path="/" />

      <div className="space-y-3">
        <Callout tone="info" title="Execution mode: ANALYSIS ONLY">
          PROJECT APEX never places, cancels or modifies orders. You place and manage every order
          manually in your authorised Tradovate interface.
        </Callout>
        <Callout tone="warn" title="Apex rule profile is UNVERIFIED">
          The values below are provisional reference values from your configuration. Until each
          rule is verified against official Apex documentation (Phase 4), no trade will be
          labelled compliant.
        </Callout>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
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

        <Panel title="Risk budget">
          <EmptyState title="Unknown">
            No account snapshot has been entered. Risk budget is treated as unknown — not as
            available.
          </EmptyState>
        </Panel>

        <Panel title="Provisional Apex reference values">
          <dl className="space-y-2 text-sm">
            {PROVISIONAL_APEX_RULES.values.map((r) => (
              <div key={r.label} className="flex justify-between gap-3">
                <dt className="text-muted">{r.label}</dt>
                <dd className="font-mono">
                  {"unit" in r ? `${r.value} ${r.unit}` : formatUsd(r.value)}
                </dd>
              </div>
            ))}
          </dl>
          <div className="mt-3">
            <StatePill state="degraded" label="Unverified" />
          </div>
        </Panel>

        <Panel title="System" className="lg:col-span-2">
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
        </Panel>

        <Panel title="Opportunities" className="lg:col-span-3">
          <EmptyState title="No ranked opportunities">
            Nothing is ranked until a strategy passes research validation on real data. Unranked
            research candidates are listed on the{" "}
            <Link href="/opportunities" className="text-accent hover:underline">Opportunity Leaderboard</Link>.
            The system never manufactures a trade to fill this panel.
          </EmptyState>
        </Panel>

        <Panel title="Build roadmap" className="lg:col-span-3">
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
        </Panel>
      </div>
    </>
  );
}
