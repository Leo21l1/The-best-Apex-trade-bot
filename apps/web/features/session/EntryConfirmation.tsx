"use client";

import type { ConfirmationCondition, ConfirmationState, EntryConfirmation } from "@apex/shared-types";
import Link from "next/link";
import { useEffect, useState } from "react";
import { cx } from "@/components/ui/primitives";
import { CollapsiblePanel } from "@/components/ui/viz";
import { usePolling } from "@/hooks/usePolling";
import { useSession } from "@/hooks/useSession";
import { api } from "@/lib/api";
import { formatEt } from "@/lib/format";
import { STATUS_MARK } from "./ReadinessPanel";

const STATE_CLS: Record<ConfirmationState, string> = {
  "LONG READY": "border-up/60 bg-up/10 text-up",
  "SHORT READY": "border-down/60 bg-down/10 text-down",
  WAIT: "border-warn/50 bg-warn/10 text-warn",
  "NO TRADE": "border-down/50 bg-down/10 text-down",
  "DATA UNAVAILABLE": "border-line bg-panel-2 text-faint",
};

const usd = (x: number | null | undefined) => (x == null ? "—" : `$${x.toLocaleString("en-US", { maximumFractionDigits: 2 })}`);

function decimals(tick: number): number {
  const s = String(tick);
  return s.includes(".") ? s.split(".")[1]!.length : 0;
}

function Condition({ c }: { c: ConfirmationCondition }) {
  const [open, setOpen] = useState(false);
  const m = STATUS_MARK[c.status];
  return (
    <li>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} data-testid={`confirm-${c.key}`}
        className="flex w-full items-start gap-2 rounded px-1 py-1 text-left hover:bg-panel-2">
        <span className={cx("mt-px inline-flex w-[4.6rem] shrink-0 justify-center rounded border px-1 font-mono text-[10px]", m.cls)}>{m.sym} {c.status}</span>
        <span className="min-w-0 flex-1">
          <span className="block text-xs text-text">
            {c.name}
            <span className={cx("ml-1 text-[10px]", c.mandatory ? "text-accent" : "text-faint")}>{c.mandatory ? "mandatory" : "optional"}</span>
          </span>
          <span className="block text-[11px] text-muted">{c.reason}</span>
        </span>
      </button>
      {open && (
        <div className="mb-1 ml-[5rem] rounded border border-line/60 p-2 font-mono text-[10px] text-faint">
          {c.confirm && c.status !== "PASS" && <p className="mb-1 font-sans text-muted">Would confirm: {c.confirm}</p>}
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(c.evidence ?? {}, null, 1)}</pre>
        </div>
      )}
    </li>
  );
}

/**
 * Live Trade Entry Confirmation: LONG READY / SHORT READY / WAIT / NO TRADE / DATA UNAVAILABLE from the
 * deterministic engine (15 conditions, mandatory per strategy). Analysis only: SAVE TRADE PLAN stores a
 * draft plan and never sends an order.
 */
export function EntryConfirmationPanel({ symbol, refreshKey }: { symbol: string; refreshKey?: number | string | null }) {
  const path = `/api/confirmation?symbol=${symbol}`;
  const { data, error } = usePolling<EntryConfirmation>(path, 20_000);
  const [fresh, setFresh] = useState<EntryConfirmation | null>(null);
  const [savedRaw, setSaved] = useState<{ id: number; symbol: string } | null>(null);
  const [saveErrRaw, setSaveErr] = useState<{ msg: string; symbol: string } | null>(null);
  const saved = savedRaw?.symbol === symbol ? savedRaw : null; // never show another symbol's result
  const saveErr = saveErrRaw?.symbol === symbol ? saveErrRaw.msg : null;
  const [saving, setSaving] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const { user } = useSession();
  useEffect(() => {
    if (refreshKey == null) return;
    const ctrl = new AbortController();
    api<EntryConfirmation>(path, { signal: ctrl.signal }).then(setFresh).catch(() => undefined);
    return () => ctrl.abort();
  }, [refreshKey, path]);
  const c = fresh && fresh.symbol === symbol && (!data || fresh.as_of >= data.as_of) ? fresh : data;
  const lv = c?.levels;
  const dp = lv ? decimals(lv.tick_size) : 2;
  const px = (x: number | null | undefined) => (x == null ? "—" : x.toFixed(dp));
  const save = async () => {
    setSaving(true); setSaveErr(null);
    try {
      const r = await api<{ id: number }>("/api/confirmation/plan", { method: "POST", body: { symbol } });
      setSaved({ id: r.id, symbol });
    } catch (e) {
      setSaveErr({ msg: (e as Error).message, symbol });
    } finally {
      setSaving(false);
    }
  };
  const question = c ? `Explain the current ${c.symbol} entry confirmation (state ${c.state}${c.strategy_id ? `, ${c.strategy_id} ${c.direction}` : ""}, as of ${c.ny_time}, 5m trigger timeframe). Which mandatory conditions are not met, what would confirm or invalidate the setup, and what are the levels and risk for 1 contract?` : "";
  const shown = c ? (showAll ? c.conditions : c.conditions.filter((x) => x.mandatory || x.status === "FAIL")) : [];

  return (
    <CollapsiblePanel id="entry-confirmation" title="Live trade entry confirmation">
      {error && !c && <p className="text-xs text-down">{error}</p>}
      {!c ? <p className="text-xs text-faint">Evaluating…</p> : (
        <div className="space-y-2 text-xs" data-testid="entry-confirmation">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cx("rounded border px-2 py-0.5 font-mono text-sm font-semibold tracking-wider", STATE_CLS[c.state])} data-testid="confirm-state">{c.state}</span>
            <span className="font-mono text-text" data-testid="confirm-count">{c.passed}/{c.total} pass</span>
            <span className={cx("rounded border px-1.5 font-mono text-[10px]", c.data_class === "LIVE" ? "border-up/40 text-up" : "border-warn/40 text-warn")} data-testid="confirm-data-class">{c.data_class}</span>
            <span className={cx("rounded border px-1.5 font-mono text-[10px]", c.actionable ? "border-up/50 text-up" : "border-line text-faint")} data-testid="confirm-actionable">
              {c.actionable ? "ACTIONABLE (manual decision)" : "INFORMATIONAL ONLY"}
            </span>
            {c.news_status === "NEWS STATUS UNKNOWN" && <span className="rounded border border-warn/40 px-1.5 font-mono text-[10px] text-warn" data-testid="confirm-news-unknown">NEWS STATUS UNKNOWN</span>}
          </div>
          <p className="text-muted" data-testid="confirm-summary">{c.summary}</p>
          <dl className="grid grid-cols-[7rem_1fr] gap-x-2 gap-y-0.5 text-[11px]">
            <dt className="text-faint">Instrument</dt><dd className="font-mono">{c.symbol}{c.contract ? ` · ${c.contract}` : ""}{c.strategy_id ? ` · ${c.strategy_id}` : ""}{c.direction ? ` · ${c.direction}` : ""}</dd>
            <dt className="text-faint">Price</dt><dd className="font-mono" data-testid="confirm-price">{c.current_price ?? "—"}{c.data_timestamp ? ` · bar ${formatEt(c.data_timestamp, true)}` : ""}</dd>
            {c.config && <><dt className="text-faint">Configuration</dt><dd className="font-mono" data-testid="confirm-config">{c.config}</dd></>}
            <dt className="text-faint">Account snapshot</dt><dd className="font-mono" data-testid="confirm-snapshot">{c.account_snapshot_at ? formatEt(c.account_snapshot_at, true) : "none — Risk Guardian status UNKNOWN"}</dd>
            <dt className="text-faint">As of</dt><dd className="font-mono">{c.ny_time}{c.data_timestamp ? ` · bar ${formatEt(c.data_timestamp)}` : ""}</dd>
            {c.regime && <><dt className="text-faint">Regime</dt><dd>{c.regime.labels.join(", ")}</dd></>}
            {c.evidence_classification && (
              <><dt className="text-faint">Historical evidence</dt><dd data-testid="confirm-evidence">{c.evidence_classification}{c.evidence_required_for_ready ? " (required for READY)" : ""} · <Link className="text-accent hover:underline" href="/research">research</Link></dd></>
            )}
            {c.next_eligible_window && !c.next_eligible_window.now && (
              <><dt className="text-faint">Next eligible window</dt><dd className="font-mono" data-testid="confirm-next-window">{c.next_eligible_window.date} {c.next_eligible_window.start_et}–{c.next_eligible_window.end_et} ET{c.next_eligible_window.in_minutes != null ? ` (in ${c.next_eligible_window.in_minutes} min)` : ""}</dd></>
            )}
            {c.expires_at && <><dt className="text-faint">Expires</dt><dd className="font-mono">{formatEt(c.expires_at)}</dd></>}
          </dl>

          {lv && (
            <div className="rounded border border-line/70 p-2" data-testid="confirm-levels">
              <p className="mb-1 text-[11px] text-faint">Levels ({lv.direction}) · {lv.trigger}</p>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-[11px] sm:grid-cols-3">
                <div><dt className="text-faint">Entry zone</dt><dd>{px(lv.entry_zone[0])}–{px(lv.entry_zone[1])}</dd></div>
                <div><dt className="text-faint">Entry (+slip)</dt><dd>{px(lv.entry)} ({px(lv.entry_with_slippage)})</dd></div>
                <div><dt className="text-faint">Stop</dt><dd className="text-down" data-testid="confirm-stop">{px(lv.stop)}</dd></div>
                <div><dt className="text-faint">T1</dt><dd className="text-up">{px(lv.t1)}</dd></div>
                <div><dt className="text-faint">T2</dt><dd className="text-up">{px(lv.t2)}</dd></div>
                <div><dt className="text-faint">Extended</dt><dd>{px(lv.extended_target)}{lv.extended_basis ? ` · ${lv.extended_basis}` : ""}</dd></div>
                <div><dt className="text-faint">Stop distance</dt><dd>{lv.stop_ticks} ticks · {lv.stop_points} pts</dd></div>
                <div><dt className="text-faint">Loss / contract</dt><dd>{usd(lv.loss_per_contract)}</dd></div>
                <div><dt className="text-faint">Reward T1 / T2</dt><dd>{usd(lv.reward_per_contract_t1)} / {usd(lv.reward_per_contract_t2)}</dd></div>
                <div>
                  <dt className="text-faint">Net R:R to T1 (min {lv.min_net_rr})</dt>
                  <dd data-testid="confirm-rr-t1" className={cx(lv.rr_t1 != null && lv.rr_t1 >= lv.min_net_rr ? "text-up" : "text-down")}>
                    {lv.rr_t1 ?? "—"} <span className="text-faint">(gross {lv.gross_rr_t1 ?? "—"})</span>
                  </dd>
                </div>
                <div>
                  <dt className="text-faint">Net R:R to T2</dt>
                  <dd data-testid="confirm-rr-t2">{lv.rr_t2 ?? "—"} <span className="text-faint">(gross {lv.gross_rr_t2 ?? "—"})</span></dd>
                </div>
                <div><dt className="text-faint">Max size (Risk Guardian)</dt><dd data-testid="confirm-max-size">{c.max_size ?? "UNKNOWN"}</dd></div>
                <div><dt className="text-faint">Invalidation</dt><dd>{px(lv.invalidation_price)}</dd></div>
              </dl>
              <p className="mt-1 text-[10px] text-faint">{lv.basis} Costs per contract: {usd(lv.costs_if_target)} if a target fills, {usd(lv.costs_per_contract)} if stopped. Apex: {c.apex_compliance}.</p>
            </div>
          )}

          {c.missing.length > 0 && (
            <div data-testid="confirm-missing">
              <p className="text-[11px] font-medium text-text">What is missing</p>
              <ul className="ml-4 list-disc text-[11px] text-muted">
                {c.missing.map((m) => <li key={m.key}><span className="text-text">{m.name}</span> ({m.status}){m.would_confirm ? ` — would confirm: ${m.would_confirm}` : ""}</li>)}
              </ul>
            </div>
          )}

          <ul className="space-y-0.5">{shown.map((x) => <Condition key={x.key} c={x} />)}</ul>
          <button type="button" className="text-[11px] text-accent hover:underline" onClick={() => setShowAll(!showAll)} data-testid="confirm-toggle-all">
            {showAll ? "Show mandatory conditions only" : `Show all ${c.total} conditions`}
          </button>

          <div className="flex flex-wrap items-center gap-2 pt-1">
            {user?.role === "admin" && (
              <button type="button" disabled={!lv || saving} onClick={() => void save()} data-testid="confirm-save-plan"
                title={lv ? "Store this analysis as a draft plan. No order is sent." : "No setup levels to save yet."}
                className="rounded border border-accent/50 px-2 py-1 text-[11px] font-semibold text-accent hover:bg-accent/10 disabled:opacity-40">
                {saving ? "Saving…" : "SAVE TRADE PLAN"}
              </button>
            )}
            <Link href={`/copilot?q=${encodeURIComponent(question)}`} data-testid="confirm-ask-claude"
              className="rounded border border-line px-2 py-1 text-[11px] text-muted hover:bg-panel-2">ASK CLAUDE TO EXPLAIN THIS SETUP</Link>
            {saved && <span className="text-[11px] text-up" data-testid="confirm-saved">Plan #{saved.id} saved (no order sent) · <Link className="underline" href="/terminal">open in Terminal</Link></span>}
            {saveErr && <span className="text-[11px] text-down">{saveErr}</span>}
          </div>
          <p className="text-[10px] text-faint">{c.disclaimer}</p>
        </div>
      )}
    </CollapsiblePanel>
  );
}
