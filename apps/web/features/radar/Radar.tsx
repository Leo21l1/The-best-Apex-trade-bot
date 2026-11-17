"use client";

import type { RadarResponse, RadarSymbol, RadarTimeframe } from "@apex/shared-types";
import { Fragment, useEffect, useMemo, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, EmptyState, Panel, QualityBadge } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { formatEt } from "@/lib/format";
import { loadWatchlist, saveWatchlist, toggleSymbol } from "@/lib/watchlist";

const REGIME: Record<RadarSymbol["regime"], { label: string; cls: string }> = {
  trend_up: { label: "Trend up", cls: "text-up border-up/30" },
  trend_down: { label: "Trend down", cls: "text-down border-down/30" },
  range: { label: "Range", cls: "text-accent border-accent/30" },
  mixed: { label: "Mixed", cls: "text-muted border-line" },
  unknown: { label: "Unknown", cls: "text-faint border-line" },
};

function TrendCell({ row }: { row: RadarTimeframe | undefined }) {
  if (!row || row.trend === null) {
    return <span className="text-faint" title={row?.note ?? "no data"}>·</span>;
  }
  const up = row.trend === "up";
  return (
    <span
      className={cx("font-mono text-xs", up ? "text-up" : row.trend === "down" ? "text-down" : "text-muted")}
      title={`EMA20 ${up ? ">" : row.trend === "down" ? "<" : "="} EMA50 · RSI ${row.rsi ?? "n/a"} · ATR ${row.atr_pct ?? "n/a"}% · rel vol ${row.rel_volume ?? "n/a"}`}
    >
      {up ? "▲" : row.trend === "down" ? "▼" : "■"}
      <span className="ml-1 text-faint">{row.rsi?.toFixed(0) ?? ""}</span>
    </span>
  );
}

const TFS = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"] as const;
const num = (v: number | null) => (v === null ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: 2 }));

export function Radar() {
  const { data, error } = usePolling<RadarResponse>("/api/scanner/radar", 10_000);
  const [query, setQuery] = useState("");
  const [onlyWatch, setOnlyWatch] = useState(false);
  const [watch, setWatch] = useState<string[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time read of browser storage after mount
  useEffect(() => setWatch(loadWatchlist()), []);

  const rows = useMemo(() => {
    const q = query.trim().toUpperCase();
    return (data?.symbols ?? []).filter(
      (s) => (!q || s.symbol.includes(q)) && (!onlyWatch || watch.includes(s.symbol)),
    );
  }, [data, query, onlyWatch, watch]);

  const star = (symbol: string) => {
    const next = toggleSymbol(watch, symbol);
    setWatch(next);
    saveWatchlist(next);
  };

  return (
    <>
      <PageHeader path="/radar">
        {data && (
          <div className="flex items-center gap-3 text-xs text-muted">
            <QualityBadge quality={data.quality} />
            <span>Scan {formatEt(data.as_of)}</span>
          </div>
        )}
      </PageHeader>
      {data?.quality === "simulated" && (
        <div className="mb-4">
          <Callout tone="warn" title="SIMULATED DATA">Readings below come from synthetic prices.</Callout>
        </div>
      )}
      {error && <div className="mb-4"><Callout tone="danger" title="Scanner request failed">{error}</Callout></div>}

      <Panel>
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter symbol…"
            aria-label="Filter symbol"
            className="w-40 rounded-md border border-line bg-panel-2 px-3 py-1.5 text-sm outline-none focus:border-accent"
          />
          <label className="flex items-center gap-2 text-sm text-muted">
            <input type="checkbox" checked={onlyWatch} onChange={(e) => setOnlyWatch(e.target.checked)} />
            Watchlist only ({watch.length})
          </label>
          <span className="ml-auto text-xs text-faint">
            ▲/▼ = EMA20 vs EMA50 on closed bars; number = RSI(14). Hover for detail.
          </span>
        </div>

        {!data ? (
          <p className="text-sm text-faint">Loading…</p>
        ) : data.symbols.length === 0 ? (
          <EmptyState title="No scan results">{data.note ?? "The scanner has not produced a scan yet."}</EmptyState>
        ) : rows.length === 0 ? (
          <EmptyState title="No symbols match the filter" />
        ) : (
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full min-w-[860px] text-sm" data-testid="radar-table">
              <thead>
                <tr className="border-b border-line text-left text-xs text-faint">
                  <th className="px-4 py-2 font-medium">Symbol</th>
                  <th className="px-2 py-2 text-right font-medium">Last</th>
                  <th className="px-2 py-2 font-medium">Regime</th>
                  <th className="px-2 py-2 font-medium">VWAP</th>
                  {TFS.map((t) => <th key={t} className="px-2 py-2 text-center font-mono font-medium">{t}</th>)}
                  <th className="px-2 py-2 text-right font-medium">Setups</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => {
                  const triggered = s.evaluations.filter((e) => e.triggered).length;
                  return (
                    <Fragment key={s.symbol}>
                      <tr className="border-b border-line/60 hover:bg-panel-2/40" data-testid={`radar-${s.symbol}`}>
                        <td className="px-4 py-2">
                          <button
                            type="button"
                            onClick={() => star(s.symbol)}
                            aria-pressed={watch.includes(s.symbol)}
                            aria-label={`Watchlist ${s.symbol}`}
                            className={cx("mr-2", watch.includes(s.symbol) ? "text-warn" : "text-faint hover:text-muted")}
                          >
                            {watch.includes(s.symbol) ? "★" : "☆"}
                          </button>
                          <button type="button" className="font-mono hover:underline" onClick={() => setOpen(open === s.symbol ? null : s.symbol)}>
                            {s.symbol}
                          </button>
                        </td>
                        <td className="px-2 py-2 text-right font-mono">{num(s.last)}</td>
                        <td className="px-2 py-2">
                          <span className={cx("rounded border px-1.5 py-px text-xs", REGIME[s.regime].cls)} title={s.regime_definition}>
                            {REGIME[s.regime].label}
                          </span>
                        </td>
                        <td className={cx("px-2 py-2 text-xs", s.vs_vwap === "above" ? "text-up" : s.vs_vwap === "below" ? "text-down" : "text-faint")}>
                          {s.vs_vwap ?? "n/a"}
                        </td>
                        {TFS.map((t) => (
                          <td key={t} className="px-2 py-2 text-center">
                            <TrendCell row={s.timeframes.find((r) => r.timeframe === t)} />
                          </td>
                        ))}
                        <td className="px-2 py-2 text-right font-mono text-xs">{triggered} / {s.evaluations.length}</td>
                      </tr>
                      {open === s.symbol && (
                        <tr className="border-b border-line/60 bg-panel-2/30">
                          <td colSpan={14} className="px-4 py-3"><Detail s={s} /></td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}

function Detail({ s }: { s: RadarSymbol }) {
  const lv = s.levels;
  const levels: [string, number | null][] = [
    ["Prior-day high", lv.prior_day_high], ["Prior-day low", lv.prior_day_low], ["Prior-day close", lv.prior_day_close],
    ["Overnight high", lv.overnight_high], ["Overnight low", lv.overnight_low],
    [`Opening range high${lv.opening_range_complete ? "" : " (forming)"}`, lv.opening_range_high],
    [`Opening range low${lv.opening_range_complete ? "" : " (forming)"}`, lv.opening_range_low],
    ["Session VWAP", s.vwap],
  ];
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div>
        <p className="mb-1 text-xs font-medium text-muted uppercase">Levels</p>
        <dl className="space-y-1 text-xs">
          {levels.map(([k, v]) => (
            <div key={k} className="flex justify-between"><dt className="text-muted">{k}</dt><dd className="font-mono">{num(v)}</dd></div>
          ))}
        </dl>
        <p className="mt-2 text-xs text-faint">{s.regime_definition} Session: {s.session}.</p>
      </div>
      <div className="lg:col-span-2">
        <p className="mb-1 text-xs font-medium text-muted uppercase">Strategy checks (latest closed 5m bar)</p>
        <ul className="space-y-1.5 text-xs">
          {s.evaluations.map((e) => (
            <li key={e.strategy_id}>
              <details>
                <summary className="cursor-pointer">
                  <span className="font-mono">{e.strategy_id}</span>{" "}
                  <span className={e.triggered ? "text-up" : "text-muted"}>
                    {e.note ?? `${e.passed}/${e.total} conditions${e.side ? ` (${e.side})` : ""}${e.triggered ? " — triggered" : ""}`}
                  </span>
                </summary>
                <ul className="mt-1 ml-4 space-y-0.5">
                  {e.conditions.map((c) => (
                    <li key={c.name} className={c.passed ? "text-muted" : "text-faint"}>
                      <span className={c.passed ? "text-up" : "text-down"}>{c.passed ? "✓" : "✗"}</span> {c.name}: {c.detail}
                    </li>
                  ))}
                </ul>
              </details>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
