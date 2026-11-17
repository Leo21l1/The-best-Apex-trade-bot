"use client";

import Link from "next/link";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, EmptyState, Panel, QualityBadge, Stat, StatePill } from "@/components/ui/primitives";
import { useSystemStatus } from "@/hooks/useSystemStatus";
import { formatEt, formatUsd, humanizeComponent } from "@/lib/format";
import { NAV } from "@/lib/nav";
import { PROVISIONAL_APEX_RULES, TRADER_PROFILE } from "@/lib/profile";

export function Overview() {
  const { status } = useSystemStatus();
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
        <Panel title="Market data" className="lg:col-span-2">
          <div className="mb-3 flex items-center gap-2">
            <QualityBadge quality="unavailable" />
            <span className="text-sm text-muted">No licensed provider connected</span>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {TRADER_PROFILE.instruments.map((i) => (
              <div key={i.symbol} className="rounded-md border border-line bg-panel-2 px-3 py-2">
                <p className="font-mono text-sm">
                  {i.symbol} <span className="text-faint">/ {i.micro}</span>
                </p>
                <p className="truncate text-xs text-faint">{i.name}</p>
                <p className="mt-1 font-mono text-sm text-faint" aria-label="price unavailable">
                  —
                </p>
              </div>
            ))}
          </div>
          <p className="mt-3 text-xs text-faint">
            Prices are never fabricated. They will appear with provider and timestamp once a
            market-data adapter is connected (Phase 2).
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

        <Panel title="Today's opportunities" className="lg:col-span-3">
          <EmptyState title="No opportunities ranked">
            The scanner and strategy engine are not active yet. Zero opportunities is a valid
            result — the system will never manufacture a trade to fill this panel.
          </EmptyState>
        </Panel>

        <Panel title="Build roadmap" className="lg:col-span-3">
          <ul className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2 lg:grid-cols-3">
            {NAV.map((n) => (
              <li key={n.href} className="flex items-center justify-between gap-2">
                <Link href={n.href} className="text-muted hover:text-text">
                  {n.label}
                </Link>
                <span className={n.phase <= 1 ? "text-xs text-up" : "text-xs text-faint"}>
                  {n.phase <= 1 ? "Available" : `Phase ${n.phase}`}
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </>
  );
}
