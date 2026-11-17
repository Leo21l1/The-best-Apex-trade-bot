"use client";

import type { AccountStateResponse, Instrument, RiskCheckResponse } from "@apex/shared-types";
import Link from "next/link";
import { useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, Panel, Stat } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { api, ApiError } from "@/lib/api";
import { money } from "@/lib/money";

const input = "w-full rounded-md border border-line bg-panel-2 px-2.5 py-1.5 text-sm font-mono outline-none focus:border-accent";

const VERDICT = {
  violation: { tone: "danger" as const, title: "Violates configured rule values" },
  cannot_confirm: { tone: "warn" as const, title: "Cannot confirm — account data missing or stale" },
  no_violation_found: { tone: "info" as const, title: "No violation of the configured values found" },
};

export function RiskGuardian() {
  const { data: instruments } = usePolling<Instrument[]>("/api/market/instruments", 3_600_000);
  const { data: acct } = usePolling<AccountStateResponse>("/api/account/state", 15_000);
  const [form, setForm] = useState({ symbol: "ES", direction: "long", entry: "", stop: "", target: "", quantity: "1" });
  const [result, setResult] = useState<RiskCheckResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inst = instruments?.find((i) => i.symbol === form.symbol);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setForm({ ...form, [k]: e.target.value });
    setResult(null);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await api<RiskCheckResponse>("/api/risk/check", {
        method: "POST",
        body: { ...form, target: form.target || null, quantity: Number(form.quantity) },
      }));
    } catch (err) {
      setResult(null);
      setError(err instanceof ApiError ? err.message : "Check failed");
    } finally {
      setBusy(false);
    }
  };

  const st = acct?.state;
  const c = result?.check;
  const verdict = c ? VERDICT[c.verdict] : null;

  return (
    <>
      <PageHeader path="/risk" />
      <div className="mb-4">
        <Callout tone="info" title="Deterministic and advisory">
          Every number here is computed from contract specifications, your configured rule values and the account
          snapshot you entered. No AI is involved. It cannot guarantee protection against account failure, and it
          places no orders.
        </Callout>
      </div>

      <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
        <Panel title="Proposed trade">
          <form onSubmit={submit} className="space-y-3" noValidate>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs text-muted">Instrument
                <select aria-label="Instrument" className={input} value={form.symbol} onChange={set("symbol")}>
                  {(instruments ?? []).map((i) => <option key={i.symbol} value={i.symbol}>{i.symbol}</option>)}
                </select>
              </label>
              <label className="text-xs text-muted">Direction
                <select aria-label="Direction" className={input} value={form.direction} onChange={set("direction")}>
                  <option value="long">Long</option>
                  <option value="short">Short</option>
                </select>
              </label>
              <label className="text-xs text-muted">Entry<input aria-label="Entry" className={input} inputMode="decimal" value={form.entry} onChange={set("entry")} /></label>
              <label className="text-xs text-muted">Stop<input aria-label="Stop" className={input} inputMode="decimal" value={form.stop} onChange={set("stop")} /></label>
              <label className="text-xs text-muted">Target (optional)<input aria-label="Target" className={input} inputMode="decimal" value={form.target} onChange={set("target")} /></label>
              <label className="text-xs text-muted">Contracts<input aria-label="Contracts" className={input} inputMode="numeric" value={form.quantity} onChange={set("quantity")} /></label>
            </div>
            {inst && (
              <p className="text-xs text-faint">
                {inst.name}: tick {inst.tick_size} = ${inst.tick_value.toFixed(2)} · point ${inst.point_value.toLocaleString("en-US")}
                {inst.is_micro ? " · counts as 0.1 standard contract" : ""}
              </p>
            )}
            {error && <p role="alert" className="text-sm text-down">{error}</p>}
            <button type="submit" disabled={busy || !form.entry || !form.stop} className="w-full rounded-md bg-accent px-3 py-2 text-sm font-medium text-bg hover:bg-accent/90 disabled:opacity-50">
              Check trade
            </button>
          </form>
        </Panel>

        <div className="space-y-4">
          <Panel title="Account context">
            {!st ? (
              <p className="text-sm text-faint">Loading…</p>
            ) : !st.known ? (
              <p className="text-sm text-warn">
                Account risk is unknown. <Link href="/account" className="underline">Enter a balance</Link> to size trades against your limits.
              </p>
            ) : (
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Stat label="Risk budget" value={st.stale ? "Unknown (stale)" : money(st.risk_budget)} />
                <Stat label="Drawdown room" value={money(st.drawdown_room)} />
                <Stat label="Daily loss remaining" value={money(st.daily_loss_remaining)} />
                <Stat label="Contracts in use" value={`${st.contracts_in_use} / ${acct?.rules.max_contracts}`} />
              </div>
            )}
            {acct && !acct.rules.verified && (
              <p className="mt-3 text-xs text-warn">Rule profile UNVERIFIED — no trade will be labelled compliant.</p>
            )}
          </Panel>

          {c && verdict && result && (
            <Panel title="Result">
              <div data-testid="risk-verdict">
                <Callout tone={verdict.tone} title={verdict.title}>{c.summary}</Callout>
              </div>
              <ol className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3" data-testid="risk-outputs">
                <li><Stat label="1. Estimated dollar risk" value={money(c.dollar_risk)} hint={`${c.stop_ticks} ticks × ${c.quantity}`} /></li>
                <li><Stat label="2. Maximum allowed size" value={c.max_size ?? "Unknown"} hint={`risk ${c.max_size_by_risk ?? "?"} · contract limit ${c.max_size_by_contract_limit}`} /></li>
                <li><Stat label="3. Reward : risk" value={c.reward_risk != null ? `${c.reward_risk.toFixed(2)} R` : "no target"} /></li>
                <li><Stat label="4. Loss after costs" value={money(c.loss_after_costs)} hint={`incl. assumed costs ${money(c.cost)}`} /></li>
                <li><Stat label="5. Budget consumed" value={c.budget_used_pct != null ? `${c.budget_used_pct}%` : "Unknown"} /></li>
                <li><Stat label="6. Rule violations" value={<span className={cx(c.violations.length ? "text-down" : "text-up")}>{c.violations.length}</span>} /></li>
              </ol>
              {c.violations.length > 0 && (
                <ul className="mt-4 space-y-1 text-sm text-down">{c.violations.map((v) => <li key={v}>✗ {v}</li>)}</ul>
              )}
              {c.warnings.length > 0 && (
                <ul className="mt-3 space-y-1 text-sm text-warn">{c.warnings.map((w) => <li key={w}>! {w}</li>)}</ul>
              )}
              <p className="mt-4 text-xs text-faint">
                Costs assume ${result.cost_assumptions.commission_per_side.toFixed(2)} commission and{" "}
                {result.cost_assumptions.slippage_ticks_per_side} tick slippage per side per contract (configurable, not
                broker quotes). {result.disclaimer}
              </p>
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}
