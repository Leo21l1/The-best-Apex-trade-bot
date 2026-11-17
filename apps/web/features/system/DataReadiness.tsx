"use client";

import type { BackfillEstimate, DataReadiness as DR } from "@apex/shared-types";
import { useState } from "react";
import { cx, Panel } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { useSession } from "@/hooks/useSession";
import { api } from "@/lib/api";
import { formatEt } from "@/lib/format";

const TONE: Record<string, string> = {
  robust: "text-up",
  ready: "text-up",
  preliminary: "text-warn",
  limited: "text-warn",
  insufficient: "text-down",
};

function Ready({ v }: { v?: string }) {
  return <span className={cx("font-mono", TONE[v ?? ""] ?? "text-faint")}>{v ?? "—"}</span>;
}

/**
 * Data Readiness: historical coverage against the 20–30 session target, live feed health, contract
 * mapping and an approved, cost-estimated backfill. Nothing downloads until an admin approves the estimate.
 */
export function DataReadinessPanel() {
  const { data, error } = usePolling<DR>("/api/data/readiness", 10_000);
  const { user } = useSession();
  const [est, setEst] = useState<BackfillEstimate | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const symbols = data?.symbols.map((s) => s.symbol) ?? [];
  const job = data?.backfill_job;
  const running = job && (job.status === "queued" || job.status === "running");

  const estimate = async () => {
    setBusy(true); setMsg(null);
    try {
      setEst(await api<BackfillEstimate>("/api/data/backfill/estimate", { method: "POST", body: { symbols, target_sessions: 25 } }));
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const approve = async () => {
    if (!est) return;
    setBusy(true); setMsg(null);
    try {
      await api("/api/data/backfill", {
        method: "POST",
        body: {
          symbols,
          target_sessions: 25,
          approved_cost_usd: est.estimated_cost_usd ?? undefined,
          acknowledge_unknown_cost: est.estimated_cost_usd == null,
        },
      });
      setMsg("Backfill approved and queued. The worker fetches a few sessions per cycle; the live stream keeps running.");
      setEst(null);
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Data readiness">
      {error && !data && <p className="text-xs text-down">{error}</p>}
      {!data ? <p className="text-xs text-faint">Loading…</p> : (
        <div className="space-y-3 text-xs" data-testid="data-readiness">
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div><dt className="text-faint">Historical provider</dt><dd className="font-mono">{data.historical_provider ?? "none"}</dd></div>
            <div><dt className="text-faint">Live provider</dt><dd className="font-mono">{data.live_provider ?? "none"}</dd></div>
            <div><dt className="text-faint">Live feed</dt><dd className="font-mono" data-testid="dr-feed">{data.live_feed.state} · {data.live_feed.class}</dd></div>
            <div><dt className="text-faint">Reconnects</dt><dd className="font-mono">{data.live_feed.reconnects ?? "—"}</dd></div>
          </dl>
          {(data.live_feed.provider_last_error || data.live_feed.last_error) && (
            <p className="text-down">Provider error: {data.live_feed.provider_last_error ?? data.live_feed.last_error}</p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-[11px]">
              <thead className="text-faint">
                <tr>
                  <th className="py-1 pr-2">Symbol</th><th className="pr-2">Contract</th><th className="pr-2">Sessions</th>
                  <th className="pr-2">Earliest / latest bar</th><th className="pr-2">Missing</th>
                  <th className="pr-2">Rel. volume</th><th className="pr-2">Volatility</th><th className="pr-2">Backtest</th><th>Last sync</th>
                </tr>
              </thead>
              <tbody>
                {data.symbols.map((s) => {
                  const ct = data.live_feed.contracts?.[s.symbol];
                  const sync = data.live_feed.last_event?.[s.symbol];
                  return (
                    <tr key={s.symbol} className="border-t border-line/50" data-testid={`dr-row-${s.symbol}`}>
                      <td className="py-1 pr-2 font-mono">{s.symbol}</td>
                      <td className="pr-2 font-mono">{ct ? `${ct.contract}` : "—"}</td>
                      <td className="pr-2 font-mono">{s.sessions_stored ?? 0}/{s.target_sessions ?? "—"}</td>
                      <td className="pr-2 font-mono">{s.earliest_bar ? formatEt(s.earliest_bar) : "—"} → {s.latest_bar ? formatEt(s.latest_bar) : "—"}</td>
                      <td className="pr-2 font-mono" title={(s.missing_sessions ?? []).join(", ")}>{s.missing_sessions?.length ?? "—"}</td>
                      <td className="pr-2"><Ready v={s.relative_volume_readiness} /></td>
                      <td className="pr-2"><Ready v={s.volatility_readiness} /></td>
                      <td className="pr-2"><Ready v={s.backtest_readiness} /></td>
                      <td className="font-mono">{sync ? formatEt(sync) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {job && (
            <div className="rounded border border-line/70 p-2" data-testid="dr-job">
              <p>Backfill {job.id}: <span className="font-mono">{job.status}</span>{job.approved_cost_usd != null ? ` · approved $${job.approved_cost_usd}` : ""}</p>
              <ul className="font-mono text-[11px] text-muted">
                {Object.entries(job.progress).map(([k, p]) => (
                  <li key={k}>{k}: fetched {p.fetched_sessions ?? 0} sessions ({p.bars ?? 0} bars), remaining {p.remaining ?? "?"}</li>
                ))}
              </ul>
              {job.errors.length > 0 && <p className="text-down">{job.errors.slice(-3).join(" · ")}</p>}
            </div>
          )}
          {user?.role === "admin" && (
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" disabled={busy || !!running || !symbols.length} onClick={() => void estimate()} data-testid="dr-estimate"
                className="rounded border border-line px-2 py-1 hover:bg-panel-2 disabled:opacity-40">Estimate backfill to 25 sessions</button>
              {est && (
                <>
                  <span data-testid="dr-estimate-result">
                    {est.items.reduce((n, i) => n + i.missing_sessions.length, 0)} missing sessions · estimated cost{" "}
                    {est.estimated_cost_usd == null ? "UNKNOWN" : `$${est.estimated_cost_usd.toFixed(4)}`}
                  </span>
                  <button type="button" disabled={busy} onClick={() => void approve()} data-testid="dr-approve"
                    className="rounded border border-accent/50 px-2 py-1 text-accent hover:bg-accent/10 disabled:opacity-40">
                    {est.estimated_cost_usd == null ? "Approve (cost unknown)" : "Approve estimate and start"}
                  </button>
                </>
              )}
            </div>
          )}
          {msg && <p className="text-muted">{msg}</p>}
          <p className="text-[10px] text-faint">{data.labels} {data.symbols[0]?.rule ?? ""} Estimates come from the provider and are not billed; your subscription may already include the data.</p>
        </div>
      )}
    </Panel>
  );
}
