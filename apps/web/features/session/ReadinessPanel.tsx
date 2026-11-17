"use client";

import type { CheckItem, CheckStatus, Readiness } from "@apex/shared-types";
import { useEffect, useState } from "react";
import { cx } from "@/components/ui/primitives";
import { CollapsiblePanel } from "@/components/ui/viz";
import { usePolling } from "@/hooks/usePolling";
import { api } from "@/lib/api";
import { formatEt } from "@/lib/format";

/** Status marks use a symbol and a word, never colour alone. */
export const STATUS_MARK: Record<CheckStatus, { sym: string; cls: string }> = {
  PASS: { sym: "✓", cls: "text-up border-up/40" },
  FAIL: { sym: "✗", cls: "text-down border-down/40" },
  WAIT: { sym: "…", cls: "text-warn border-warn/40" },
  UNKNOWN: { sym: "?", cls: "text-faint border-line" },
};
const OVERALL: Record<string, string> = {
  "REVIEW SETUP": "border-up/50 bg-up/10 text-up",
  WAIT: "border-warn/50 bg-warn/10 text-warn",
  "NO TRADE": "border-down/50 bg-down/10 text-down",
};
const STYLES = ["intraday", "scalping", "momentum", "orb", "afternoon"] as const;

function Row({ i }: { i: CheckItem }) {
  const [open, setOpen] = useState(false);
  const m = STATUS_MARK[i.status];
  return (
    <li>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} data-testid={`check-${i.key}`}
        className="flex w-full items-start gap-2 rounded px-1 py-1 text-left hover:bg-panel-2">
        <span className={cx("mt-px inline-flex w-[4.6rem] shrink-0 justify-center rounded border px-1 font-mono text-[10px]", m.cls)}>{m.sym} {i.status}</span>
        <span className="min-w-0 flex-1">
          <span className="block text-xs text-text">{i.name}</span>
          <span className="block text-[11px] text-muted">{i.reason}</span>
        </span>
      </button>
      {open && (
        <div className="mb-1 ml-[5rem] rounded border border-line/60 p-2 font-mono text-[10px] text-faint">
          {i.calculation && <p className="mb-1 font-sans text-muted">Method: {i.calculation}</p>}
          <pre className="whitespace-pre-wrap break-all">{JSON.stringify(i.evidence ?? {}, null, 1)}</pre>
        </div>
      )}
    </li>
  );
}

/**
 * Compact Trading Readiness panel: New York time, session, data status, relative volume, regime,
 * the eight checklist conditions with evidence, and WAIT / REVIEW SETUP / NO TRADE. Refreshes every
 * 20 s and whenever ``refreshKey`` changes (e.g. a new bar arrived on the chart).
 */
export function ReadinessPanel({ symbol, refreshKey }: { symbol: string; refreshKey?: number | string | null }) {
  const [style, setStyle] = useState<(typeof STYLES)[number]>("intraday");
  const { data, error } = usePolling<Readiness>(`/api/session/readiness?symbol=${symbol}&style=${style}`, 20_000);
  const [fresh, setFresh] = useState<Readiness | null>(null);
  useEffect(() => {
    if (refreshKey == null) return;
    const ctrl = new AbortController();
    api<Readiness>(`/api/session/readiness?symbol=${symbol}&style=${style}`, { signal: ctrl.signal }).then(setFresh).catch(() => undefined);
    return () => ctrl.abort();
  }, [refreshKey, symbol, style]);
  const r = fresh && fresh.checklist.symbol === symbol && fresh.checklist.style === style &&
    (!data || fresh.checklist.as_of >= data.checklist.as_of) ? fresh : data;
  const c = r?.checklist;
  return (
    <CollapsiblePanel id={`readiness`} title="Trading readiness" actions={
      <select aria-label="Trading style" value={style} onChange={(e) => setStyle(e.target.value as typeof style)} className="rounded border border-line bg-panel-2 px-1 py-0.5 text-[11px]">
        {STYLES.map((s) => <option key={s}>{s}</option>)}
      </select>}>
      {error && <p className="text-xs text-down">{error}</p>}
      {!r || !c ? <p className="text-xs text-faint">Evaluating…</p> : (
        <div className="space-y-2 text-xs" data-testid="readiness">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cx("rounded border px-2 py-0.5 font-mono text-sm font-semibold tracking-wider", OVERALL[c.status])} data-testid="readiness-status">{c.status}</span>
            <span className="font-mono text-text" data-testid="readiness-count">{c.passed}/{c.total} pass</span>
            {c.direction && <span className="text-muted">structure {c.direction}</span>}
          </div>
          <dl className="grid grid-cols-[6.5rem_1fr] gap-x-2 gap-y-0.5 text-[11px]">
            <dt className="text-faint">New York</dt><dd className="font-mono" data-testid="readiness-ny">{c.ny_time}</dd>
            <dt className="text-faint">Session</dt><dd>{r.session.window.label}{r.session.window.start ? ` (${r.session.window.start}-${r.session.window.end})` : ""}; next: {r.session.next_transition.label} at {r.session.next_transition.at_et} ({r.session.next_transition.in_minutes} min)</dd>
            <dt className="text-faint">Market</dt><dd className={r.session.market.open ? "" : "text-warn"}>{r.session.market.open ? "Open" : "Closed"} · {r.session.market.reason}</dd>
            <dt className="text-faint">Data</dt><dd className={["STALE", "UNAVAILABLE"].includes(r.data.status) ? "text-down" : ""}>{r.data.status}{r.data.last_bar ? ` · last bar ${formatEt(r.data.last_bar)}` : ""}{r.data.reason ? ` · ${r.data.reason}` : ""}</dd>
            <dt className="text-faint">Rel. volume</dt><dd>{c.relative_volume.available ? `${c.relative_volume.ratio}× (${c.relative_volume.current_volume} vs ${c.relative_volume.reference_volume}, ${c.relative_volume.window_et} ET)` : c.relative_volume.reason}</dd>
            <dt className="text-faint">Volatility</dt><dd>{c.relative_volatility.available ? `${c.relative_volatility.label} (${c.relative_volatility.ratio}×)` : c.relative_volatility.reason}</dd>
            <dt className="text-faint">Regime</dt><dd title={c.regime.definition}>{c.regime.label}{c.regime.efficiency_ratio_2h != null ? ` · ER ${c.regime.efficiency_ratio_2h}` : ""} · {c.regime.vwap_crosses_today} VWAP crosses today</dd>
            {r.setup && <><dt className="text-faint">Setup</dt><dd>{r.setup.strategy_id} {r.setup.direction} @ {r.setup.entry}, stop {r.setup.stop}, target {r.setup.targets[0]}</dd></>}
            <dt className="text-faint">News</dt><dd className={r.news.status === "inside_window" ? "text-down" : ""}>{r.news.summary}</dd>
          </dl>
          {c.critical.length > 0 && <ul className="rounded border border-down/40 bg-down/5 p-1.5 text-down">{c.critical.map((x) => <li key={x}>⛔ {x}</li>)}</ul>}
          <ul className="space-y-0.5">{c.items.map((i) => <Row key={i.key} i={i} />)}</ul>
          {(c.failures.length > 0 || c.unknowns.length > 0) && (
            <p className="text-[11px] text-faint">Failing: {c.failures.join(", ") || "none"} · Unknown: {c.unknowns.join(", ") || "none"}</p>
          )}
          <p className="text-[10px] text-faint">{c.disclaimer}</p>
        </div>
      )}
    </CollapsiblePanel>
  );
}
