"use client";

import { useState } from "react";
import { Callout, cx, Panel } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { api, ApiError } from "@/lib/api";
import { formatEt } from "@/lib/format";

interface Policy {
  checked_on: string;
  api_sync_permitted: boolean;
  summary: string;
  findings: { source: string; url: string; finding: string }[];
  not_done: string[];
  to_enable: string;
  manual_workflow: string[];
}

interface ImportResponse {
  dry_run: boolean;
  applied: boolean;
  blocked_by_errors: boolean;
  files: { name: string; layout: string | null; errors: string[]; trades?: number; fills?: number; rows?: number; days?: number; positions?: number; skipped_unfilled?: number; open_lots?: number }[];
  trades: { parsed: number; new: number; duplicates: number; imported: number; matched_to_plans: { trade_id: number; plan_id: number }[] };
  duplicates: { file: string; reason: string; symbol: string; entry_time: string }[];
  balances: { parsed: number; new: number; conflicts: { date: string; imported: string; stored: string[] }[] };
  positions: { rows: { symbol: string; contract: string; quantity: number; avg_price: number; open_pnl: number | null }[]; open_pnl: number | null } | null;
  positions_note: string | null;
  open_lots_from_fills: { contract: string; quantity: number; avg_price: number; opened_at: string }[];
  reconciliation: { date: string; cash_realized: number; journal_gross: number; cash_fees: number; journal_fees: number; pnl_matches: boolean; fees_match: boolean; end_balance: number | null }[];
  warnings: string[];
  preview: { symbol: string; contract: string; direction: string; qty: number; entry_time: string; exit_time: string; entry_price: number; exit_price: number; net_pnl: number }[];
}

const LAYOUT: Record<string, string> = {
  performance: "Performance (round trips)", generic: "Generic trades", fills: "Orders / fills (paired FIFO)",
  cash: "Cash history", positions: "Open positions", balances: "End-of-day balances",
};

/**
 * Read-only broker statement import. Explains why automatic API sync is not enabled, then imports
 * several Tradovate exports at once with a mandatory preview, duplicate detection, plan matching
 * and reconciliation against the cash history.
 */
export function BrokerImport({ onImported }: { onImported: () => void }) {
  const { data: policy } = usePolling<Policy>("/api/statements/policy", 3_600_000);
  const [files, setFiles] = useState<{ name: string; csv: string }[]>([]);
  const [tz, setTz] = useState("America/New_York");
  const [res, setRes] = useState<ImportResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showPolicy, setShowPolicy] = useState(false);

  const pick = async (list: FileList | null) => {
    if (!list) return;
    const read = await Promise.all([...list].slice(0, 10).map(async (f) => ({ name: f.name, csv: await f.text() })));
    setFiles(read);
    setRes(null);
  };
  const run = async (dry: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const r = await api<ImportResponse>("/api/statements/import", { method: "POST", body: { files, timezone: tz, dry_run: dry } });
      setRes(r);
      if (r.applied) onImported();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Broker statement import (Tradovate, read-only)">
      {policy && !policy.api_sync_permitted && (
        <div className="mb-3">
          <Callout tone="info" title="Automatic account sync is not enabled">
            {policy.summary}{" "}
            <button type="button" className="text-accent hover:underline" onClick={() => setShowPolicy((v) => !v)} data-testid="policy-toggle">
              {showPolicy ? "Hide details" : `Why? (checked ${policy.checked_on})`}
            </button>
          </Callout>
          {showPolicy && (
            <div className="mt-2 space-y-2 rounded border border-line p-3 text-xs" data-testid="broker-policy">
              {policy.findings.map((f) => (
                <p key={f.url}><a href={f.url} target="_blank" rel="noreferrer noopener" className="text-accent hover:underline">{f.source}</a>: {f.finding}</p>
              ))}
              <p className="text-muted">{policy.not_done.join(" ")}</p>
              <p className="text-faint">To enable sync later: {policy.to_enable}</p>
            </div>
          )}
        </div>
      )}
      <ol className="mb-3 list-decimal space-y-1 pl-5 text-xs text-muted">
        {(policy?.manual_workflow ?? []).map((s) => <li key={s}>{s}</li>)}
      </ol>
      <div className="flex flex-wrap items-center gap-3">
        <input type="file" accept=".csv,text/csv" multiple aria-label="Broker CSV files" onChange={(e) => void pick(e.target.files)}
          className="text-xs file:mr-2 file:rounded file:border file:border-line file:bg-panel-2 file:px-2 file:py-1 file:text-text" />
        <label className="text-xs text-muted">Export time zone{" "}
          <select aria-label="Export time zone" value={tz} onChange={(e) => setTz(e.target.value)} className="rounded border border-line bg-panel-2 px-1 py-0.5">
            {["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "UTC", "Europe/London"].map((z) => <option key={z}>{z}</option>)}
          </select>
        </label>
        <button type="button" disabled={!files.length || busy} onClick={() => void run(true)}
          className="rounded border border-line px-3 py-1.5 text-xs hover:border-accent disabled:opacity-50">Check files</button>
        <button type="button" disabled={!res || !res.dry_run || res.blocked_by_errors || busy} onClick={() => void run(false)}
          className="rounded bg-accent/90 px-3 py-1.5 text-xs font-medium text-bg hover:bg-accent disabled:opacity-40" data-testid="broker-apply">
          Import {res?.dry_run ? `${res.trades.new} trade(s), ${res.balances.new} balance(s)` : ""}
        </button>
      </div>
      {files.length > 0 && !res && <p className="mt-2 text-xs text-faint">{files.length} file(s) selected: {files.map((f) => f.name).join(", ")}. Check the files first; nothing is saved until you import.</p>}
      {error && <p className="mt-2 text-xs text-down">{error}</p>}
      {res && <Result r={res} />}
    </Panel>
  );
}

function Result({ r }: { r: ImportResponse }) {
  return (
    <div className="mt-3 space-y-3 text-xs" data-testid="broker-result">
      <p className={cx("font-medium", r.applied ? "text-up" : r.blocked_by_errors ? "text-down" : "text-text")}>
        {r.applied ? `Imported ${r.trades.imported} trade(s) and ${r.balances.new} balance(s); reviews generated.`
          : r.blocked_by_errors ? "Nothing can be imported until the errors below are fixed."
            : `Preview: ${r.trades.new} new trade(s), ${r.trades.duplicates} duplicate(s) skipped, ${r.balances.new} new balance(s).`}
      </p>
      <table className="w-full text-left">
        <thead className="text-faint"><tr><th className="py-1">File</th><th>Detected report</th><th>Contents</th></tr></thead>
        <tbody>
          {r.files.map((f) => (
            <tr key={f.name} className="border-t border-line/60 align-top">
              <td className="py-1 font-mono">{f.name}</td>
              <td className={f.layout ? "" : "text-down"}>{f.layout ? LAYOUT[f.layout] : "unrecognised"}</td>
              <td>
                {[f.fills !== undefined && `${f.fills} fills`, f.trades !== undefined && `${f.trades} trades`, f.skipped_unfilled ? `${f.skipped_unfilled} unfilled orders skipped` : null,
                  f.days !== undefined && `${f.days} day(s)`, f.positions !== undefined && `${f.positions} position(s)`, f.rows !== undefined && f.days === undefined && `${f.rows} row(s)`].filter(Boolean).join(" · ")}
                {f.errors.map((e) => <p key={e} className="text-down">{e}</p>)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {r.warnings.map((w) => <p key={w} className="text-warn">⚠ {w}</p>)}
      {r.preview.length > 0 && r.dry_run && (
        <div>
          <p className="mb-1 font-medium text-muted">New trades</p>
          <ul className="space-y-0.5 font-mono">{r.preview.map((t, i) => (
            <li key={i}>{t.direction} {t.qty} {t.contract} {t.entry_price}→{t.exit_price} · {formatEt(t.entry_time, true)} · <span className={t.net_pnl >= 0 ? "text-up" : "text-down"}>{t.net_pnl >= 0 ? "+" : ""}{t.net_pnl.toFixed(2)}</span></li>
          ))}</ul>
        </div>
      )}
      {r.duplicates.length > 0 && (
        <details><summary className="cursor-pointer text-muted">{r.duplicates.length} duplicate(s) skipped</summary>
          <ul className="mt-1 space-y-0.5">{r.duplicates.map((d, i) => <li key={i}>{d.file}: {d.symbol} {formatEt(d.entry_time, true)}: {d.reason}</li>)}</ul>
        </details>
      )}
      {r.trades.matched_to_plans.length > 0 && <p className="text-up">Matched to your plans: {r.trades.matched_to_plans.map((m) => `trade #${m.trade_id} ↔ plan #${m.plan_id}`).join(", ")}</p>}
      {r.reconciliation.length > 0 && (
        <div data-testid="reconciliation">
          <p className="mb-1 font-medium text-muted">Reconciliation with cash history</p>
          <table className="w-full text-left font-mono">
            <thead className="font-sans text-faint"><tr><th>Date</th><th>Realised (cash)</th><th>Gross (journal)</th><th>Fees (cash)</th><th>Fees (journal)</th><th>EOD balance</th></tr></thead>
            <tbody>{r.reconciliation.map((x) => (
              <tr key={x.date} className="border-t border-line/60">
                <td>{x.date}</td>
                <td>{x.cash_realized.toFixed(2)}</td><td className={x.pnl_matches ? "text-up" : "text-warn"}>{x.journal_gross.toFixed(2)} {x.pnl_matches ? "✓" : "≠"}</td>
                <td>{x.cash_fees.toFixed(2)}</td><td className={x.fees_match ? "text-up" : "text-warn"}>{x.journal_fees.toFixed(2)} {x.fees_match ? "✓" : "≠"}</td>
                <td>{x.end_balance?.toFixed(2) ?? "—"}</td>
              </tr>
            ))}</tbody>
          </table>
          <p className="mt-1 text-faint">A mismatch can mean a missing export day, estimated fees, or a cash history dated by calendar day rather than the 18:00 ET trading day.</p>
        </div>
      )}
      {r.balances.conflicts.length > 0 && <p className="text-warn">Not imported (a different balance is already stored): {r.balances.conflicts.map((c) => `${c.date} ${c.imported} vs ${c.stored.join("/")}`).join("; ")}</p>}
      {r.positions && r.positions.rows.length > 0 && (
        <p>Open positions: {r.positions.rows.map((p) => `${p.quantity > 0 ? "+" : ""}${p.quantity} ${p.contract} @ ${p.avg_price}`).join(", ")}{r.positions.open_pnl != null ? ` · open P&L ${r.positions.open_pnl.toFixed(2)}` : ""}{r.positions_note ? `. ${r.positions_note}` : ""}</p>
      )}
      {r.open_lots_from_fills.length > 0 && <p className="text-muted">Still open in the fills file (not journaled): {r.open_lots_from_fills.map((l) => `${l.quantity > 0 ? "+" : ""}${l.quantity} ${l.contract} @ ${l.avg_price}`).join(", ")}</p>}
    </div>
  );
}
