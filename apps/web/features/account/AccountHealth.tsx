"use client";

import type {
  AccountSnapshot,
  AccountStateResponse,
  CsvImportResponse,
  Rule,
  RulesResponse,
} from "@apex/shared-types";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, EmptyState, Panel, Stat, StatePill } from "@/components/ui/primitives";
import { useSession } from "@/hooks/useSession";
import { api, ApiError } from "@/lib/api";
import { formatAge, formatEt } from "@/lib/format";
import { money, progress } from "@/lib/money";

const input = "w-full rounded-md border border-line bg-panel-2 px-2.5 py-1.5 text-sm outline-none focus:border-accent";
const button = "rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-bg hover:bg-accent/90 disabled:opacity-50";
const ghost = "rounded-md border border-line px-3 py-1.5 text-sm text-muted hover:bg-panel-2 disabled:opacity-50";

export function AccountHealth() {
  const { user } = useSession();
  const admin = user?.role === "admin";
  const [acct, setAcct] = useState<AccountStateResponse | null>(null);
  const [rules, setRules] = useState<RulesResponse | null>(null);
  const [snaps, setSnaps] = useState<AccountSnapshot[]>([]);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [a, r, s] = await Promise.all([
        api<AccountStateResponse>("/api/account/state"),
        api<RulesResponse>("/api/rules"),
        api<AccountSnapshot[]>("/api/account/snapshots?limit=50"),
      ]);
      setAcct(a);
      setRules(r);
      setSnaps(s);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    // reload() only sets state after its awaits resolve, never synchronously.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
    const t = setInterval(() => void reload(), 30_000);
    return () => clearInterval(t);
  }, [reload]);

  const st = acct?.state;
  const rs = acct?.rules;

  return (
    <>
      <PageHeader path="/account" />
      {error && <div className="mb-4"><Callout tone="danger" title="Request failed">{error}</Callout></div>}
      {rules && !rules.verified && (
        <div className="mb-3">
          <Callout tone="warn" title="Apex rule profile is UNVERIFIED">
            {rules.unverified.length} required rule(s) have no verification record. Verify each value against
            the official Apex help centre in the rule table below. Until then, no trade is labelled compliant.
          </Callout>
        </div>
      )}
      {st?.known && st.stale && (
        <div className="mb-3">
          <Callout tone="warn" title="Account data is stale">
            Last reading {st.snapshot_age_seconds != null ? formatAge(Math.floor(st.snapshot_age_seconds)) : ""}. Enter
            a fresh balance; remaining risk is treated as unknown until then.
          </Callout>
        </div>
      )}
      {(st?.drawdown_breached || st?.daily_loss_hit) && (
        <div className="mb-3">
          <Callout tone="danger" title={st.drawdown_breached ? "At or below the drawdown threshold" : "Daily loss limit reached"}>
            Based on the figures you entered. Confirm against Tradovate immediately.
          </Callout>
        </div>
      )}

      {!acct ? (
        <p className="text-sm text-faint">Loading…</p>
      ) : !st?.known ? (
        <Panel title="Account">
          <EmptyState title="Account risk is unknown">
            No balance has been entered. PROJECT APEX has no broker connection: enter your balance below or
            import end-of-day balances from a CSV. Until then, risk budget is treated as unknown — not available.
          </EmptyState>
        </Panel>
      ) : (
        <AccountSummary acct={acct} />
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <SnapshotForm onSaved={reload} />
        <CsvImport onSaved={reload} />
      </div>

      <div className="mt-4">
        <Panel title="Snapshot history">
          {snaps.length === 0 ? (
            <p className="text-sm text-faint">No snapshots yet.</p>
          ) : (
            <div className="-mx-4 overflow-x-auto">
              <table className="w-full min-w-[640px] text-sm" data-testid="snapshot-table">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-faint">
                    {["As of (ET)", "Trading date", "Source", "Balance", "Unrealised", "Type", "Positions", ""].map((h) => (
                      <th key={h} className="px-3 py-2 font-medium first:pl-4">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {snaps.map((s) => (
                    <tr key={s.id} className="border-b border-line/60 last:border-0">
                      <td className="py-2 pr-3 pl-4 font-mono text-xs">{formatEt(s.as_of, true)}</td>
                      <td className="px-3 py-2 font-mono text-xs">{s.trading_date}</td>
                      <td className="px-3 py-2 text-xs text-muted">{s.source}</td>
                      <td className="px-3 py-2 font-mono">{money(Number(s.balance))}</td>
                      <td className="px-3 py-2 font-mono">{money(Number(s.unrealized_pnl), true)}</td>
                      <td className="px-3 py-2 text-xs">{s.is_eod ? "End of day" : "Intraday"}</td>
                      <td className="px-3 py-2 font-mono text-xs">
                        {s.positions.map((p) => `${p.symbol} ${p.quantity > 0 ? "+" : ""}${p.quantity}`).join(", ") || "—"}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {admin && (
                          <button
                            type="button"
                            className="text-xs text-down hover:underline"
                            onClick={async () => {
                              if (!window.confirm("Delete this snapshot?")) return;
                              await api(`/api/account/snapshots/${s.id}`, { method: "DELETE" }).catch(() => undefined);
                              void reload();
                            }}
                          >
                            Delete
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      {rules && (
        <div className="mt-4">
          <RuleTable rules={rules} admin={admin} onChanged={(r) => { setRules(r); void reload(); }} />
        </div>
      )}
      {rs && <p className="mt-3 text-xs text-faint">{acct?.disclaimer}</p>}
    </>
  );
}

function AccountSummary({ acct }: { acct: AccountStateResponse }) {
  const { state: st, rules: rs } = acct;
  const target = rs.starting_balance + rs.profit_target;
  const threshold = st.drawdown_threshold ?? 0;
  const pos = progress(st.equity ?? 0, threshold, target);
  return (
    <Panel
      title="Account"
      actions={
        <span className="text-xs text-faint">
          {st.source} · {st.snapshot_at ? formatEt(st.snapshot_at, true) : ""}
        </span>
      }
    >
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6" data-testid="account-stats">
        <Stat label="Balance" value={money(st.balance)} hint="closed P&L" />
        <Stat label="Equity" value={money(st.equity)} hint={`unrealised ${money(st.unrealized_pnl, true)}`} />
        <Stat label="EOD drawdown threshold" value={money(st.drawdown_threshold)} hint={`highest EOD ${money(st.highest_eod_balance)}`} />
        <Stat label="Drawdown room" value={<span data-testid="drawdown-room">{money(st.drawdown_room)}</span>} />
        <Stat label="Daily loss remaining" value={money(st.daily_loss_remaining)} hint={`today ${money(st.day_pnl, true)}`} />
        <Stat
          label="Risk budget"
          value={<span data-testid="risk-budget" className={cx(st.stale && "text-warn")}>{st.stale ? "Unknown (stale)" : money(st.risk_budget)}</span>}
          hint="min(room, daily remaining)"
        />
      </div>

      <div className="mt-5">
        <div className="mb-1 flex justify-between text-xs text-muted">
          <span>Threshold {money(st.drawdown_threshold)}</span>
          <span>{st.target_reached ? "Profit target reached" : `${money(st.profit_to_target)} to target`}</span>
          <span>Target {money(target)}</span>
        </div>
        <div className="relative h-2 overflow-hidden rounded-full bg-panel-2" role="meter" aria-label="Equity between drawdown threshold and profit target" aria-valuenow={Math.round(pos * 100)} aria-valuemin={0} aria-valuemax={100}>
          <div className={cx("h-full rounded-full", pos < 0.2 ? "bg-down" : pos < 0.4 ? "bg-warn" : "bg-up")} style={{ width: `${pos * 100}%` }} />
        </div>
        <p className="mt-2 text-xs text-faint">
          EOD drawdown: the threshold moves only with end-of-day balances (highest EOD − {money(rs.max_drawdown)}), never with
          intraday peaks. It is treated as enforced in real time against equity (conservative). Contracts in use:{" "}
          {st.contracts_in_use} of {rs.max_contracts}.
        </p>
      </div>

      {st.warnings.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-muted">
          {st.warnings.map((w) => <li key={w}>• {w}</li>)}
        </ul>
      )}
    </Panel>
  );
}

interface PosRow { symbol: string; quantity: string; avg_price: string; stop: string }
const SYMBOLS = ["ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K", "CL", "MCL", "GC", "MGC"];

function SnapshotForm({ onSaved }: { onSaved: () => void }) {
  const [balance, setBalance] = useState("");
  const [upl, setUpl] = useState("0");
  const [eod, setEod] = useState(false);
  const [note, setNote] = useState("");
  const [positions, setPositions] = useState<PosRow[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await api("/api/account/snapshots", {
        method: "POST",
        body: {
          balance, unrealized_pnl: eod ? "0" : upl || "0", is_eod: eod, note: note || null,
          positions: eod ? [] : positions.map((p) => ({
            symbol: p.symbol, quantity: Number(p.quantity), avg_price: p.avg_price, stop: p.stop || null,
          })),
        },
      });
      setMsg({ ok: true, text: "Snapshot saved." });
      setBalance("");
      setUpl("0");
      setPositions([]);
      setNote("");
      onSaved();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Could not save" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Enter account snapshot">
      <form onSubmit={submit} className="space-y-3" noValidate>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-xs text-muted">Balance (closed)
            <input className={input} inputMode="decimal" value={balance} onChange={(e) => setBalance(e.target.value)} required placeholder="50000.00" />
          </label>
          <label className="text-xs text-muted">Unrealised P&L
            <input className={input} inputMode="decimal" value={eod ? "0" : upl} disabled={eod} onChange={(e) => setUpl(e.target.value)} />
          </label>
        </div>
        <label className="flex items-center gap-2 text-sm text-muted">
          <input type="checkbox" checked={eod} onChange={(e) => setEod(e.target.checked)} />
          This is today&apos;s end-of-day balance (all positions closed)
        </label>
        {!eod && (
          <div>
            <p className="mb-1 text-xs text-muted">Open positions</p>
            {positions.map((p, i) => (
              <div key={i} className="mb-2 grid grid-cols-[5.5rem_4.5rem_1fr_1fr_auto] gap-2">
                <select aria-label="Position symbol" className={input} value={p.symbol} onChange={(e) => setPositions(positions.map((x, j) => (j === i ? { ...x, symbol: e.target.value } : x)))}>
                  {SYMBOLS.map((s) => <option key={s}>{s}</option>)}
                </select>
                <input aria-label="Quantity (negative = short)" className={input} placeholder="+/-qty" value={p.quantity} onChange={(e) => setPositions(positions.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)))} />
                <input aria-label="Average price" className={input} placeholder="avg price" value={p.avg_price} onChange={(e) => setPositions(positions.map((x, j) => (j === i ? { ...x, avg_price: e.target.value } : x)))} />
                <input aria-label="Stop price" className={input} placeholder="stop (optional)" value={p.stop} onChange={(e) => setPositions(positions.map((x, j) => (j === i ? { ...x, stop: e.target.value } : x)))} />
                <button type="button" aria-label="Remove position" className="px-1 text-faint hover:text-down" onClick={() => setPositions(positions.filter((_, j) => j !== i))}>✕</button>
              </div>
            ))}
            <button type="button" className={ghost} onClick={() => setPositions([...positions, { symbol: "ES", quantity: "1", avg_price: "", stop: "" }])}>
              Add position
            </button>
          </div>
        )}
        <label className="block text-xs text-muted">Note (optional)
          <input className={input} value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
        </label>
        {msg && <p role="status" className={cx("text-sm", msg.ok ? "text-up" : "text-down")}>{msg.text}</p>}
        <button type="submit" className={button} disabled={busy || !balance}>Save snapshot</button>
        <p className="text-xs text-faint">Copy the values from Tradovate. They are stored as user-entered, not broker-confirmed.</p>
      </form>
    </Panel>
  );
}

function CsvImport({ onSaved }: { onSaved: () => void }) {
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<CsvImportResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (dry: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const r = await api<CsvImportResponse>("/api/account/import-csv", { method: "POST", body: { csv: text, dry_run: dry } });
      setPreview(r);
      if (!dry && r.imported > 0) {
        setText("");
        onSaved();
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Import failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Import end-of-day balances (CSV)">
      <p className="mb-2 text-xs text-muted">
        Needs a date column (e.g. <code>Date</code>, <code>Trade Date</code>) and a balance column (e.g.{" "}
        <code>End of Day Balance</code>, <code>Balance</code>). Preview first; nothing is imported if any row fails.
      </p>
      <input
        type="file"
        accept=".csv,text/csv"
        aria-label="CSV file"
        className="mb-2 block text-xs text-muted file:mr-3 file:rounded file:border file:border-line file:bg-panel-2 file:px-2 file:py-1 file:text-muted"
        onChange={async (e) => {
          const f = e.target.files?.[0];
          if (f) {
            setText(await f.text());
            setPreview(null);
          }
        }}
      />
      <textarea
        aria-label="CSV content"
        className={cx(input, "h-28 font-mono text-xs")}
        placeholder={"Date,End of Day Balance\n2026-11-09,50400.00\n2026-11-10,50900.00"}
        value={text}
        onChange={(e) => { setText(e.target.value); setPreview(null); }}
      />
      <div className="mt-2 flex gap-2">
        <button type="button" className={ghost} disabled={busy || !text} onClick={() => void run(true)}>Preview</button>
        <button type="button" className={button} disabled={busy || !preview || preview.errors.length > 0 || preview.rows.length === 0 || !preview.dry_run} onClick={() => void run(false)}>
          Import {preview?.dry_run ? preview.rows.length : ""} rows
        </button>
      </div>
      {error && <p className="mt-2 text-sm text-down">{error}</p>}
      {preview && (
        <div className="mt-3 text-xs" data-testid="csv-preview">
          {preview.errors.length > 0 ? (
            <ul className="space-y-0.5 text-down">{preview.errors.slice(0, 10).map((e) => <li key={e}>{e}</li>)}</ul>
          ) : preview.dry_run ? (
            <p className="text-muted">
              {preview.rows.length} row(s) using columns “{preview.date_column}” and “{preview.balance_column}”:{" "}
              {preview.rows.slice(0, 5).map(([d, b]) => `${d} ${money(Number(b))}`).join(" · ")}
              {preview.rows.length > 5 ? " …" : ""}
            </p>
          ) : (
            <p className="text-up">Imported {preview.imported} end-of-day balance(s).</p>
          )}
        </div>
      )}
    </Panel>
  );
}

function RuleTable({ rules, admin, onChanged }: { rules: RulesResponse; admin: boolean; onChanged: (r: RulesResponse) => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  return (
    <Panel
      title={`Rule profile — ${rules.label}`}
      actions={<StatePill state={rules.verified ? "ok" : "degraded"} label={rules.verified ? "Verified" : `${rules.unverified.length} unverified`} />}
    >
      <p className="mb-3 text-xs text-muted">
        Each rule needs the official Apex page it was checked against. Changing a value clears its verification.
        PROJECT APEX does not fetch or interpret the rules for you.
      </p>
      <div className="-mx-4 overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm" data-testid="rule-table">
          <thead>
            <tr className="border-b border-line text-left text-xs text-faint">
              <th className="py-2 pr-3 pl-4 font-medium">Rule</th>
              <th className="px-3 py-2 font-medium">Value</th>
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2 font-medium">Source</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {rules.rules.map((r) => (
              <RuleRowView key={r.key} r={r} admin={admin} open={editing === r.key} onToggle={() => setEditing(editing === r.key ? null : r.key)} onChanged={onChanged} />
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function RuleRowView({ r, admin, open, onToggle, onChanged }: { r: Rule; admin: boolean; open: boolean; onToggle: () => void; onChanged: (r: RulesResponse) => void }) {
  const [value, setValue] = useState(r.value);
  const [url, setUrl] = useState(r.source_url ?? "");
  const [appliesTo, setAppliesTo] = useState(r.applies_to ?? "50K EOD Evaluation (Tradovate)");
  const [version, setVersion] = useState(r.version ?? "");
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const display = r.kind === "money" && r.value ? money(Number(r.value)) : r.value || "—";
  const call = async (fn: () => Promise<RulesResponse>) => {
    setErr(null);
    try {
      onChanged(await fn());
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Failed");
    }
  };

  return (
    <>
      <tr className="border-b border-line/60 align-top" data-testid={`rule-${r.key}`}>
        <td className="py-2 pr-3 pl-4">{r.label}{!r.required && <span className="ml-1 text-xs text-faint">(optional)</span>}</td>
        <td className={cx("px-3 py-2", r.kind === "text" ? "text-xs text-muted" : "font-mono")}>{display}</td>
        <td className="px-3 py-2"><StatePill state={r.verified ? "ok" : "degraded"} label={r.verified ? "Verified" : "Unverified"} /></td>
        <td className="px-3 py-2 text-xs">
          {r.verified && r.source_url ? (
            <a href={r.source_url} target="_blank" rel="noreferrer noopener" className="text-accent hover:underline">
              {r.version} · {r.verified_at ? formatEt(r.verified_at, true) : ""}
            </a>
          ) : r.suggested_source_url ? (
            <a href={r.suggested_source_url} target="_blank" rel="noreferrer noopener" className="text-faint hover:underline">check here</a>
          ) : "—"}
        </td>
        <td className="px-3 py-2 text-right">
          {admin && <button type="button" className="text-xs text-accent hover:underline" onClick={onToggle}>{open ? "Close" : "Edit / verify"}</button>}
        </td>
      </tr>
      {open && (
        <tr className="border-b border-line/60 bg-panel-2/30">
          <td colSpan={5} className="px-4 py-3">
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="space-y-2">
                <label className="block text-xs text-muted">Value
                  {r.kind === "text" ? (
                    <textarea className={cx(input, "h-20")} value={value} onChange={(e) => setValue(e.target.value)} />
                  ) : (
                    <input className={input} value={value} onChange={(e) => setValue(e.target.value)} />
                  )}
                </label>
                <button type="button" className={ghost} onClick={() => void call(() => api<RulesResponse>(`/api/rules/${r.key}`, { method: "PUT", body: { value } }))}>
                  Save value
                </button>
              </div>
              <div className="space-y-2">
                <label className="block text-xs text-muted">Official source URL (apextraderfunding.com)
                  <input className={input} value={url} onChange={(e) => setUrl(e.target.value)} placeholder={r.suggested_source_url ?? "https://apextraderfunding.com/help-center/…"} />
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <label className="text-xs text-muted">Applies to
                    <input className={input} value={appliesTo} onChange={(e) => setAppliesTo(e.target.value)} />
                  </label>
                  <label className="text-xs text-muted">Version / page date
                    <input className={input} value={version} onChange={(e) => setVersion(e.target.value)} placeholder="e.g. updated 2026-10-01" />
                  </label>
                </div>
                <label className="flex items-start gap-2 text-xs text-muted">
                  <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} className="mt-0.5" />
                  I checked the current value against this official page.
                </label>
                <div className="flex gap-2">
                  <button
                    type="button"
                    className={button}
                    disabled={!confirm || !url || !version}
                    onClick={() => void call(() => api<RulesResponse>(`/api/rules/${r.key}/verify`, {
                      method: "POST", body: { source_url: url, applies_to: appliesTo, version, confirm: true },
                    }))}
                  >
                    Mark verified
                  </button>
                  {r.verified && (
                    <button type="button" className={ghost} onClick={() => void call(() => api<RulesResponse>(`/api/rules/${r.key}/unverify`, { method: "POST" }))}>
                      Remove verification
                    </button>
                  )}
                </div>
              </div>
            </div>
            {err && <p className="mt-2 text-sm text-down">{err}</p>}
          </td>
        </tr>
      )}
    </>
  );
}
