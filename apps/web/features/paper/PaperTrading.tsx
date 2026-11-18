"use client";

import type { PaperOrder, PaperState } from "@apex/shared-types";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, Panel, Stat } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { formatEt } from "@/lib/format";
import { money } from "@/lib/money";
import { AutopilotPanel } from "./Autopilot";

const SYMBOLS = ["ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K", "CL", "MCL", "GC", "MGC"];
const input = "w-full rounded-md border border-line bg-panel-2 px-2.5 py-1.5 text-sm font-mono outline-none focus:border-accent";
const STATUS: Record<PaperOrder["status"], string> = {
  working: "text-accent", pending: "text-faint", partially_filled: "text-warn", filled: "text-up",
  cancelled: "text-faint", rejected: "text-down",
};

export function PaperTrading() {
  const [st, setSt] = useState<PaperState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [form, setForm] = useState({ symbol: "MES", side: "buy", type: "market", qty: "1", price: "", stop_loss: "", take_profit: "" });

  const load = useCallback(async () => {
    try {
      setSt(await api<PaperState>("/api/paper"));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    // load() sets state only after its await resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    const t = setInterval(() => void load(), 5000);
    return () => clearInterval(t);
  }, [load]);

  const act = async (path: string, body?: unknown) => {
    setMsg(null);
    try {
      const r = await api<{ result: PaperOrder | null; account: PaperState } | PaperState>(path, { method: "POST", body });
      if ("account" in r) {
        setSt(r.account);
        const o = r.result as PaperOrder | null;
        if (o && o.status === "rejected") setMsg(`Rejected: ${o.reason}`);
        else if (o) setMsg(`Order ${o.id} ${o.status}.`);
      } else setSt(r);
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Request failed");
    }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const num = (v: string) => (v ? Number(v) : null);
    void act("/api/paper/orders", {
      symbol: form.symbol, side: form.side, type: form.type, qty: Number(form.qty),
      price: form.type === "market" ? null : num(form.price), stop_loss: num(form.stop_loss), take_profit: num(form.take_profit),
    });
  };

  const s = st?.summary;
  const working = st?.orders.filter((o) => ["working", "pending", "partially_filled"].includes(o.status)) ?? [];

  return (
    <>
      <PageHeader path="/paper" />
      <div className="mb-4"><AutopilotPanel /></div>
      <div className="mb-4 space-y-3">
        <Callout tone="info" title="Isolated simulator">
          {st?.notice ?? "Simulated account."} Orders fill against stored market data with conservative rules (next-bar
          open plus slippage, limits need a trade-through, partial fills by volume). Limits mirror your configured rule values.
        </Callout>
        {s?.status === "breached" && <Callout tone="danger" title="Paper account breached the EOD drawdown threshold">Reset the account to start again.</Callout>}
        {s?.status === "halted_daily_loss" && <Callout tone="warn" title="Daily loss limit reached">New orders are rejected until the next trading day (18:00 ET).</Callout>}
        {error && <Callout tone="danger" title="Request failed">{error}</Callout>}
      </div>

      {s && (
        <Panel>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-7" data-testid="paper-summary">
            <Stat label="Balance" value={money(s.balance)} />
            <Stat label="Unrealised" value={<span className={s.unrealized >= 0 ? "text-up" : "text-down"}>{money(s.unrealized, true)}</span>} />
            <Stat label="Equity" value={money(s.equity)} />
            <Stat label="Day P&L" value={money(s.day_pnl, true)} />
            <Stat label="Drawdown room" value={money(s.drawdown_room)} hint={`threshold ${money(s.drawdown_threshold)}`} />
            <Stat label="Daily loss left" value={money(s.daily_loss_remaining)} />
            <Stat label="Contracts" value={`${s.contracts_in_use} / ${s.max_contracts}`} />
          </div>
        </Panel>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-[20rem_1fr]">
        <Panel title="Order ticket (simulated)">
          <form onSubmit={submit} className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <label className="text-xs text-muted">Symbol
                <select aria-label="Paper symbol" className={input} value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })}>
                  {SYMBOLS.map((x) => <option key={x}>{x}</option>)}
                </select>
              </label>
              <label className="text-xs text-muted">Quantity<input aria-label="Paper quantity" className={input} value={form.qty} onChange={(e) => setForm({ ...form, qty: e.target.value })} /></label>
              <label className="text-xs text-muted">Side
                <select aria-label="Paper side" className={input} value={form.side} onChange={(e) => setForm({ ...form, side: e.target.value })}>
                  <option value="buy">Buy</option><option value="sell">Sell</option>
                </select>
              </label>
              <label className="text-xs text-muted">Type
                <select aria-label="Paper order type" className={input} value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
                  <option value="market">Market</option><option value="limit">Limit</option><option value="stop">Stop</option>
                </select>
              </label>
              {form.type !== "market" && (
                <label className="col-span-2 text-xs text-muted">{form.type === "limit" ? "Limit price" : "Stop trigger"}
                  <input aria-label="Paper price" className={input} value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} />
                </label>
              )}
              <label className="text-xs text-muted">Stop loss<input aria-label="Paper stop loss" className={input} value={form.stop_loss} onChange={(e) => setForm({ ...form, stop_loss: e.target.value })} /></label>
              <label className="text-xs text-muted">Take profit<input aria-label="Paper take profit" className={input} value={form.take_profit} onChange={(e) => setForm({ ...form, take_profit: e.target.value })} /></label>
            </div>
            <button type="submit" className="w-full rounded-md bg-accent px-3 py-2 text-sm font-medium text-bg hover:bg-accent/90">Submit simulated order</button>
            {msg && <p role="status" className={cx("text-sm", msg.startsWith("Rejected") ? "text-down" : "text-muted")} data-testid="paper-msg">{msg}</p>}
            <div className="flex gap-2 pt-1">
              <button type="button" className="flex-1 rounded-md border border-line px-3 py-1.5 text-xs text-muted hover:bg-panel-2" onClick={() => void act("/api/paper/flatten")}>Flatten all</button>
              <button type="button" className="flex-1 rounded-md border border-line px-3 py-1.5 text-xs text-muted hover:bg-panel-2"
                onClick={() => { if (window.confirm("Reset the paper account? History is cleared.")) void act("/api/paper/reset"); }}>Reset account</button>
            </div>
          </form>
        </Panel>

        <div className="min-w-0 space-y-4">
          <Panel title="Positions">
            {!st?.positions.length ? <p className="text-sm text-faint">Flat.</p> : (
              <table className="w-full text-sm"><thead><tr className="text-left text-xs text-faint"><th className="py-1">Symbol</th><th>Qty</th><th>Avg</th><th>Last</th><th>Opened (ET)</th></tr></thead>
                <tbody className="font-mono">{st.positions.map((p) => (
                  <tr key={p.symbol} className="border-t border-line/60"><td className="py-1">{p.symbol}</td><td className={p.qty > 0 ? "text-up" : "text-down"}>{p.qty > 0 ? `+${p.qty}` : p.qty}</td><td>{p.avg_price}</td><td>{p.last ?? "—"}</td><td className="text-xs">{p.opened_at ? formatEt(p.opened_at) : ""}</td></tr>
                ))}</tbody></table>
            )}
          </Panel>
          <Panel title={`Working orders (${working.length})`}>
            {working.length === 0 ? <p className="text-sm text-faint">None.</p> : (
              <ul className="space-y-1 text-sm" data-testid="paper-working">
                {working.map((o) => (
                  <li key={o.id} className="flex items-center justify-between gap-2 font-mono text-xs">
                    <span>#{o.id} {o.role !== "entry" ? `[${o.role.replace("_", " ")}] ` : ""}{o.side} {o.qty - o.filled_qty} {o.symbol} {o.type}{o.price ? ` @ ${o.price}` : ""} <span className={STATUS[o.status]}>{o.status}</span></span>
                    {o.role === "entry" && <button type="button" className="text-faint hover:text-down" onClick={() => void act(`/api/paper/orders/${o.id}/cancel`)}>Cancel</button>}
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <div className="grid gap-4 xl:grid-cols-2">
            <Panel title="Order history">
              <ul className="max-h-64 space-y-1 overflow-auto font-mono text-[11px]">
                {(st?.orders ?? []).slice(0, 40).map((o) => (
                  <li key={o.id}>#{o.id} {o.side} {o.filled_qty}/{o.qty} {o.symbol} {o.type} <span className={STATUS[o.status]}>{o.status}</span>{o.avg_fill_price ? ` @ ${o.avg_fill_price}` : ""}{o.reason ? <span className="text-faint"> — {o.reason}</span> : null}</li>
                ))}
              </ul>
            </Panel>
            <Panel title="Closed trades">
              {!st?.closed.length ? <p className="text-sm text-faint">None yet. Closed trades are copied to the journal.</p> : (
                <ul className="max-h-64 space-y-1 overflow-auto font-mono text-[11px]">
                  {st.closed.map((c) => (
                    <li key={c.id}>{c.symbol} {c.direction} {c.qty} {c.entry_price}→{c.exit_price} <span className={c.gross_pnl - c.fees >= 0 ? "text-up" : "text-down"}>{money(c.gross_pnl - c.fees, true)}</span></li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
          <Panel title="Activity">
            <ul className="max-h-40 space-y-0.5 overflow-auto text-[11px] text-muted">{(st?.events ?? []).map((e, i) => <li key={i}><span className="font-mono text-faint">{formatEt(e.ts)}</span> {e.text}</li>)}</ul>
          </Panel>
        </div>
      </div>
    </>
  );
}
