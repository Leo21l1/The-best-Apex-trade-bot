"use client";

import type { SmcScan, SmcScanCard } from "@apex/shared-types";
import Link from "next/link";
import { useState } from "react";
import { cx, Panel } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { formatEt } from "@/lib/format";

function Card({ c }: { c: SmcScanCard }) {
  const tone = c.direction === "long" ? "border-up/40" : c.direction === "short" ? "border-down/40" : "border-line";
  return (
    <li className={cx("rounded-lg border bg-panel-2/40 p-3 text-xs", tone)} data-testid={`smc-card-${c.symbol}`}>
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <Link href={`/smc?symbol=${c.symbol}`} className="font-mono text-sm font-semibold text-text hover:text-accent">{c.symbol}</Link>
        <span className={c.direction === "long" ? "text-up" : c.direction === "short" ? "text-down" : "text-muted"}>{c.headline}</span>
        <span className="ml-auto rounded border border-warn/40 px-1 font-mono text-[10px] text-warn">{c.validation}</span>
      </div>
      <p className="text-muted">{c.setups.length ? c.setups.join(" · ") : "No named setup"}</p>
      <p className="mt-1 text-faint">
        {c.families.map((f) => `${f.family}: ${f.vote > 0 ? "bull" : f.vote < 0 ? "bear" : "—"}`).join(" · ")} · score {c.score}/{c.max_score}
      </p>
      {c.entry_zone && (
        <p className="mt-1 font-mono text-text">
          entry {c.entry_zone[0]}–{c.entry_zone[1]} · stop {c.stop} · tgt {c.targets.map((t) => t.price).join(" / ")} · R:R {c.reward_risk ?? "—"}
        </p>
      )}
      {c.invalidation && <p className="text-faint">{c.invalidation}</p>}
      <p className="mt-1 text-faint">News: {c.news.summary} · Risk: {c.risk?.known ? `budget $${c.risk.risk_budget}` : "account unknown"}</p>
      {c.not_ranked_because.length > 0 && <p className="mt-1 text-warn">Not ranked: {c.not_ranked_because.join(" ")}</p>}
    </li>
  );
}

/** SMC opportunity scan across every supported instrument (refreshes each minute). */
export function SmcScanPanel() {
  const { data, error } = usePolling<SmcScan>("/api/smc/scan", 60_000);
  const [showWatch, setShowWatch] = useState(true);
  return (
    <Panel title="Smart-money setup scan (all instruments)" actions={data && <span className="text-[11px] text-faint">as of {formatEt(data.as_of)}</span>}>
      {error && <p className="text-xs text-down">{error}</p>}
      {!data ? <p className="text-xs text-faint">Scanning…</p> : (
        <div data-testid="smc-scan">
          <p className="mb-2 text-xs text-faint">{data.note}</p>
          <p className="mb-1 text-[11px] uppercase tracking-wider text-muted">Ranked ({data.ranked.length})</p>
          {data.ranked.length === 0
            ? <p className="mb-3 text-xs text-muted">No setup passes every check right now. That is a valid result.</p>
            : <ul className="mb-3 grid gap-2 lg:grid-cols-2">{data.ranked.map((c) => <Card key={c.symbol} c={c} />)}</ul>}
          <button type="button" onClick={() => setShowWatch((v) => !v)} className="mb-1 text-[11px] uppercase tracking-wider text-muted hover:text-text">
            {showWatch ? "▾" : "▸"} Watch list, not ranked ({data.watch.length})
          </button>
          {showWatch && <ul className="grid gap-2 lg:grid-cols-2">{data.watch.map((c) => <Card key={c.symbol} c={c} />)}</ul>}
        </div>
      )}
    </Panel>
  );
}
