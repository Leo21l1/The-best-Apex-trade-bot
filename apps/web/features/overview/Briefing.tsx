"use client";

import type { BriefingResponse } from "@apex/shared-types";
import Link from "next/link";
import { cx, Panel, QualityBadge } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { formatEt } from "@/lib/format";

const PHASE: Record<BriefingResponse["phase"], string> = {
  premarket: "Premarket", live: "Live session", postmarket: "Post-market", closed: "Market closed",
};
const DOT = { ok: "bg-up", warning: "bg-warn", critical: "bg-down" };
const fmt = (v: number | null) => (v == null ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: 2 }));

export function Briefing() {
  const { data: b } = usePolling<BriefingResponse>("/api/briefing", 30_000);
  if (!b) return null;
  return (
    <Panel
      title={`Session briefing · ${PHASE[b.phase]}`}
      className="lg:col-span-3"
      actions={<span className="flex items-center gap-2"><QualityBadge quality={b.data.quality} /><span className="text-xs text-faint">{formatEt(b.as_of)}</span></span>}
    >
      <div className="grid gap-5 lg:grid-cols-3" data-testid="briefing">
        <div>
          <p className="mb-2 text-xs font-medium tracking-wide text-muted uppercase">Checklist</p>
          <ul className="space-y-1.5 text-sm">
            {b.checklist.map((c) => (
              <li key={c.text} className="flex gap-2">
                <span className={cx("mt-1.5 h-2 w-2 shrink-0 rounded-full", DOT[c.status])} aria-label={c.status} />
                <span className={c.status === "ok" ? "text-muted" : "text-text"}>{c.text}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="min-w-0 lg:col-span-2">
          <p className="mb-2 text-xs font-medium tracking-wide text-muted uppercase">Key levels (prior day / overnight)</p>
          {b.markets.length === 0 ? (
            <p className="text-sm text-faint">No scan available, so no levels. Configure a market data provider.</p>
          ) : (
            <div className="-mx-4 overflow-x-auto">
              <table className="w-full min-w-[560px] text-xs">
                <thead>
                  <tr className="text-left text-faint">
                    {["Symbol", "Last", "Regime", "PDH", "PDL", "PDC", "ONH", "ONL"].map((h) => <th key={h} className="px-2 py-1 font-medium first:pl-4">{h}</th>)}
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {b.markets.map((m) => (
                    <tr key={m.symbol} className="border-t border-line/60">
                      <td className="py-1 pr-2 pl-4">{m.symbol}</td>
                      <td className="px-2">{fmt(m.last)}</td>
                      <td className="px-2 font-sans text-muted">{m.regime.replace("_", " ")}</td>
                      <td className="px-2">{fmt(m.prior_day_high)}</td>
                      <td className="px-2">{fmt(m.prior_day_low)}</td>
                      <td className="px-2">{fmt(m.prior_day_close)}</td>
                      <td className="px-2">{fmt(m.overnight_high)}</td>
                      <td className="px-2">{fmt(m.overnight_low)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="mt-3 text-xs text-faint">
            {b.events_today.length} event(s) today · {b.active_candidates} active research candidate(s) · ranked
            opportunities: {b.ranked_opportunities}.{" "}
            <Link className="text-accent hover:underline" href={`/copilot?q=${encodeURIComponent("Prepare my session briefing: regime, key levels, events and my risk budget.")}`}>
              Ask the copilot for a written briefing →
            </Link>
          </p>
        </div>
      </div>
    </Panel>
  );
}
