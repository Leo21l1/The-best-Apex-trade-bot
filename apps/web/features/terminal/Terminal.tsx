"use client";

import type {
  AccountStateResponse, Bar, ChartEvent, Instrument, LiveStatus, PaperState, RiskCheckResponse, Timeframe, TradePlanOut,
} from "@apex/shared-types";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, Panel, QualityBadge, Stat } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { api, ApiError } from "@/lib/api";
import { formatPrice } from "@/lib/candles";
import { formatEt } from "@/lib/format";
import { money } from "@/lib/money";
import { ticketMath, unrealized } from "@/lib/ticket";
import { CandleChart, type OverlayToggles, type PriceLineSpec } from "../charts/CandleChart";
import { NewsMarkerList, NewsPanel, OverlayToggleBar, SignalBadge, useOverlays } from "../charts/overlayParts";
import { useCandles } from "../charts/useCandles";

const SYMBOLS = ["ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K", "CL", "MCL", "GC", "MGC"];
const input = "w-full rounded-md border border-line bg-panel-2 px-2.5 py-1.5 text-sm font-mono outline-none focus:border-accent";
const num = (v: string) => (v.trim() === "" ? null : Number(v));

export function Terminal() {
  const [symbol, setSymbol] = useState("MES");
  const [tf, setTf] = useState<Timeframe>("1m");
  const [toggles, setToggles] = useState<OverlayToggles>({ ema: true, vwap: true, levels: false, signals: true, news: true });
  const [event, setEvent] = useState<ChartEvent | null>(null);
  const [hover, setHover] = useState<Bar | null>(null);
  const { data: instruments } = usePolling<Instrument[]>("/api/market/instruments", 3_600_000);
  const { data: paper, error: paperError } = usePolling<PaperState>("/api/paper", 3000);
  const { data: acct } = usePolling<AccountStateResponse>("/api/account/state", 15_000);
  const { data: live } = usePolling<LiveStatus>("/api/execution/live-status", 3_600_000);
  const { data: plans } = usePolling<TradePlanOut[]>("/api/plans", 10_000);
  const { bars, provenance } = useCandles(symbol, tf);
  const overlays = useOverlays(symbol, tf);
  const inst = instruments?.find((i) => i.symbol === symbol);
  const tick = inst?.tick_size ?? 0.25;
  const last = bars[bars.length - 1]?.c ?? null;

  const [side, setSide] = useState<"long" | "short">("long");
  const [qty, setQty] = useState("1");
  const [type, setType] = useState<"market" | "limit">("market");
  const [entry, setEntry] = useState("");
  const [stop, setStop] = useState("");
  const [target, setTarget] = useState("");
  // Prefill from a confluence plan link (/terminal?symbol=NQ&side=sell&stop=..&target=..). Nothing is submitted.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    /* eslint-disable react-hooks/set-state-in-effect -- one-time read of the URL after mount */
    if (q.get("symbol")) setSymbol(q.get("symbol")!.toUpperCase());
    if (q.get("side")) setSide(q.get("side") === "sell" ? "short" : "long");
    if (q.get("stop")) setStop(q.get("stop")!);
    if (q.get("target")) setTarget(q.get("target")!);
    /* eslint-enable react-hooks/set-state-in-effect */
  }, []);
  const [thesis, setThesis] = useState("");
  const [preview, setPreview] = useState<RiskCheckResponse | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "err" | "info"; text: string; steps?: string[] } | null>(null);
  const [busy, setBusy] = useState(false);

  const refEntry = type === "limit" ? num(entry) : last;
  const math = ticketMath(side, refEntry, num(stop), num(target), Number(qty), tick, inst?.tick_value ?? 1.25);
  const pos = paper?.positions.find((p) => p.symbol === symbol);
  const working = paper?.orders.filter((o) => ["working", "pending", "partially_filled"].includes(o.status)) ?? [];

  const workingKey = JSON.stringify(working.map((w) => [w.id, w.price, w.status]));
  const lines = useMemo<PriceLineSpec[]>(() => {
    const out: PriceLineSpec[] = [];
    if (pos) {
      out.push({ price: pos.avg_price, title: `${pos.qty > 0 ? "LONG" : "SHORT"} ${Math.abs(pos.qty)} @`, color: "#38bdf8" });
      for (const o of working.filter((w) => w.symbol === symbol && w.role !== "entry" && w.price != null)) {
        out.push({ price: o.price!, title: o.role === "stop_loss" ? "STOP" : "TARGET", color: o.role === "stop_loss" ? "#f05a5e" : "#34d399" });
      }
    } else {
      if (num(stop) != null) out.push({ price: num(stop)!, title: "plan stop", color: "#f05a5e", dashed: true });
      if (num(target) != null) out.push({ price: num(target)!, title: "plan target", color: "#34d399", dashed: true });
      if (type === "limit" && num(entry) != null) out.push({ price: num(entry)!, title: "plan entry", color: "#38bdf8", dashed: true });
    }
    return out;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pos?.avg_price, pos?.qty, workingKey, stop, target, entry, type, symbol]);

  const useSignal = () => {
    const sig = overlays?.label.signal;
    if (!sig) return;
    setSide(sig.direction);
    setStop(String(sig.stop));
    setTarget(String(sig.targets[0] ?? ""));
    setThesis(`${sig.strategy_id} ${sig.direction === "long" ? "BUY" : "SHORT"} signal (research candidate, unvalidated).`);
  };

  const planBody = () => ({
    symbol, direction: side, quantity: Number(qty), entry: type === "limit" ? num(entry) : null,
    stop: num(stop), target: num(target), thesis: thesis || null,
    strategy_id: overlays?.label.signal?.strategy_id ?? "discretionary",
  });

  const doPreview = async () => {
    setMsg(null);
    if (refEntry == null || num(stop) == null) return setMsg({ tone: "err", text: "Enter a stop (and a limit price for limit orders)." });
    try {
      setPreview(await api<RiskCheckResponse>("/api/risk/check", { method: "POST", body: {
        symbol, direction: side, entry: String(Math.round(refEntry / tick) * tick), stop: stop, target: target || null, quantity: Number(qty),
      } }));
    } catch (e) {
      setMsg({ tone: "err", text: e instanceof ApiError ? e.message : "Preview failed" });
    }
  };

  const submit = async (mode: "paper" | "manual") => {
    setBusy(true);
    setMsg(null);
    try {
      const plan = await api<TradePlanOut>("/api/plans", { method: "POST", body: planBody() });
      if (mode === "paper") {
        await api(`/api/plans/${plan.id}/execute-paper`, { method: "POST" });
        setMsg({ tone: "ok", text: `Plan #${plan.id} sent to the paper simulator. Market orders fill at the next bar's open.` });
      } else {
        const r = await api<{ next_steps: string[] }>(`/api/plans/${plan.id}/mark-manual`, { method: "POST" });
        setMsg({ tone: "info", text: `Plan #${plan.id} saved for manual execution in Tradovate:`, steps: [
          `${side === "long" ? "BUY" : "SELL"} ${qty} ${symbol} ${type === "limit" ? `LIMIT ${entry}` : "MARKET"}, stop ${stop}${target ? `, target ${target}` : ""}`,
          ...r.next_steps,
        ] });
      }
    } catch (e) {
      setMsg({ tone: "err", text: e instanceof ApiError ? e.message : "Request failed" });
    } finally {
      setBusy(false);
    }
  };

  const closePosition = async () => {
    if (!pos) return;
    try {
      await api("/api/paper/orders", { method: "POST", body: { symbol, side: pos.qty > 0 ? "sell" : "buy", type: "market", qty: Math.abs(pos.qty) } });
      setMsg({ tone: "ok", text: "Close order sent (fills at the next bar's open)." });
    } catch (e) {
      setMsg({ tone: "err", text: e instanceof ApiError ? e.message : "Close failed" });
    }
  };

  const s = paper?.summary;
  const budget = acct?.state.risk_budget;
  const emergency: string[] = [];
  if (s?.status === "breached") emergency.push("Paper account breached its drawdown threshold.");
  if (s?.status === "halted_daily_loss") emergency.push("Paper daily loss limit reached: new orders are rejected until 18:00 ET.");
  if (s && s.daily_loss_remaining < 0.25 * (paper?.config.daily_loss_limit ?? 1000)) emergency.push(`Paper daily loss allowance nearly used: ${money(s.daily_loss_remaining)} left.`);
  if (acct?.state.drawdown_breached) emergency.push("Apex account (entered figures) is at or below the drawdown threshold.");
  if (acct?.state.daily_loss_hit) emergency.push("Apex account (entered figures) has hit the daily loss limit.");

  return (
    <>
      <PageHeader path="/terminal">
        <span className="flex items-center gap-2 text-xs"><QualityBadge quality={provenance?.quality ?? "unavailable"} /><SignalBadge label={overlays?.label} /></span>
      </PageHeader>
      <div className="mb-3 space-y-2">
        {emergency.map((e) => <Callout key={e} tone="danger" title={e} />)}
        {provenance?.quality === "simulated" && <Callout tone="warn" title="SIMULATED prices: paper trading only" />}
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_22rem]">
        <div className="min-w-0 space-y-4">
          <Panel>
            <div className="mb-2 flex flex-wrap items-center gap-3">
              <select aria-label="Terminal instrument" className="rounded-md border border-line bg-panel-2 px-2 py-1.5 font-mono text-sm" value={symbol}
                onChange={(e) => { setSymbol(e.target.value); setPreview(null); setStop(""); setTarget(""); setEntry(""); }}>
                {SYMBOLS.map((x) => <option key={x}>{x}</option>)}
              </select>
              <div role="group" aria-label="Terminal timeframe" className="flex overflow-hidden rounded-md border border-line">
                {(["1m", "5m", "15m", "1h"] as Timeframe[]).map((t) => (
                  <button key={t} type="button" aria-pressed={t === tf} onClick={() => setTf(t)}
                    className={cx("px-2.5 py-1.5 font-mono text-xs", t === tf ? "bg-accent/15 text-accent" : "text-muted hover:bg-panel-2")}>{t}</button>
                ))}
              </div>
              <span className="font-mono text-xl" data-testid="terminal-last">{last != null ? formatPrice(last, tick) : "—"}</span>
              {hover && <span className="font-mono text-[11px] text-faint">O {hover.o} H {hover.h} L {hover.l} C {hover.c}</span>}
              <div className="ml-auto"><OverlayToggleBar value={toggles} onChange={setToggles} /></div>
            </div>
            {bars.length > 0 ? (
              <CandleChart bars={bars} tick={tick} daily={false} seriesKey={`t:${symbol}:${tf}`} onHover={setHover} height="h-[420px] w-full"
                overlays={overlays?.symbol === symbol && overlays.timeframe === tf ? overlays : null} toggles={toggles} lines={lines} onEventClick={setEvent} />
            ) : <p className="py-20 text-center text-sm text-faint">{provenance?.note ?? "Loading bars…"}</p>}
            {toggles.news && <div className="mt-2"><NewsMarkerList events={(overlays?.events ?? []).filter((e) => e.t >= (bars[0]?.t ?? 0))} onSelect={setEvent} /></div>}
            <p className="mt-2 text-[11px] text-faint">Market depth: not available (no licensed depth-of-market feed is connected).</p>
          </Panel>

          <Panel title="Positions (paper)">
            {!paper?.positions.length ? <p className="text-sm text-faint">Flat.</p> : (
              <table className="w-full text-sm" data-testid="terminal-positions">
                <thead><tr className="text-left text-xs text-faint"><th className="py-1">Symbol</th><th>Qty</th><th>Avg</th><th>Last</th><th>Unrealised</th><th /></tr></thead>
                <tbody className="font-mono">{paper.positions.map((p) => {
                  const pv = instruments?.find((i) => i.symbol === p.symbol)?.point_value ?? 0;
                  const u = unrealized(p.qty, p.avg_price, p.last, pv);
                  return (
                    <tr key={p.symbol} className="border-t border-line/60">
                      <td className="py-1.5">{p.symbol}</td><td className={p.qty > 0 ? "text-up" : "text-down"}>{p.qty > 0 ? `+${p.qty}` : p.qty}</td>
                      <td>{p.avg_price}</td><td>{p.last ?? "—"}</td>
                      <td className={u == null ? "" : u >= 0 ? "text-up" : "text-down"}>{money(u, true)}</td>
                      <td className="text-right">{p.symbol === symbol && <button type="button" onClick={() => void closePosition()} className="rounded border border-line px-2 py-0.5 text-xs text-muted hover:bg-panel-2">Close</button>}</td>
                    </tr>
                  );
                })}</tbody>
              </table>
            )}
          </Panel>

          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title={`Working orders (${working.length})`}>
              {working.length === 0 ? <p className="text-sm text-faint">None.</p> : (
                <ul className="space-y-1 font-mono text-xs" data-testid="terminal-working">
                  {working.map((o) => (
                    <li key={o.id} className="flex justify-between gap-2">
                      <span>#{o.id} {o.role !== "entry" ? `[${o.role.replace("_", " ")}] ` : ""}{o.side} {o.qty - o.filled_qty} {o.symbol} {o.type}{o.price ? ` @ ${o.price}` : ""}</span>
                      {o.role === "entry" && <button type="button" className="text-faint hover:text-down" onClick={() => void api(`/api/paper/orders/${o.id}/cancel`, { method: "POST" })}>Cancel</button>}
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
            <Panel title="Order history">
              <ul className="max-h-48 space-y-1 overflow-auto font-mono text-[11px]">
                {(paper?.orders ?? []).slice(0, 30).map((o) => (
                  <li key={o.id}>#{o.id} {o.side} {o.filled_qty}/{o.qty} {o.symbol} {o.type} <span className={o.status === "filled" ? "text-up" : o.status === "rejected" ? "text-down" : "text-muted"}>{o.status}</span>{o.avg_fill_price ? ` @ ${o.avg_fill_price}` : ""}</li>
                ))}
              </ul>
              <Link href="/paper" className="mt-2 inline-block text-[11px] text-accent hover:underline">Full simulator view →</Link>
            </Panel>
          </div>

          <Panel title="Trade plans">
            {!plans?.length ? <p className="text-sm text-faint">No plans yet.</p> : (
              <ul className="space-y-1 text-xs" data-testid="terminal-plans">
                {plans.slice(0, 10).map((p) => (
                  <li key={p.id} className="flex flex-wrap justify-between gap-2 font-mono">
                    <span>#{p.id} {p.direction} {p.quantity} {p.symbol} stop {p.stop}{p.target ? ` tgt ${p.target}` : ""} · signal {p.signal_snapshot?.label ?? "—"}</span>
                    <span className="text-muted">{p.status.replace("_", " ")} · {formatEt(p.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel title="Order ticket">
            <div className="grid grid-cols-2 gap-2" role="group" aria-label="Direction">
              <button type="button" aria-pressed={side === "long"} onClick={() => setSide("long")}
                className={cx("rounded-md border py-2 text-sm font-semibold transition-colors", side === "long" ? "border-up bg-up/15 text-up" : "border-line text-muted hover:bg-panel-2")}>BUY</button>
              <button type="button" aria-pressed={side === "short"} onClick={() => setSide("short")}
                className={cx("rounded-md border py-2 text-sm font-semibold transition-colors", side === "short" ? "border-down bg-down/15 text-down" : "border-line text-muted hover:bg-panel-2")}>SELL</button>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <label className="text-xs text-muted">Contracts<input aria-label="Ticket quantity" className={input} inputMode="numeric" value={qty} onChange={(e) => setQty(e.target.value)} /></label>
              <label className="text-xs text-muted">Order type
                <select aria-label="Ticket order type" className={input} value={type} onChange={(e) => setType(e.target.value as typeof type)}>
                  <option value="market">Market</option><option value="limit">Limit</option>
                </select>
              </label>
              {type === "limit" && <label className="col-span-2 text-xs text-muted">Limit price<input aria-label="Ticket limit price" className={input} value={entry} onChange={(e) => setEntry(e.target.value)} /></label>}
              <label className="text-xs text-muted">Stop-loss<input aria-label="Ticket stop" className={input} value={stop} onChange={(e) => setStop(e.target.value)} /></label>
              <label className="text-xs text-muted">Take-profit<input aria-label="Ticket target" className={input} value={target} onChange={(e) => setTarget(e.target.value)} /></label>
            </div>
            {overlays?.label.signal && <button type="button" onClick={useSignal} className="mt-2 text-xs text-accent hover:underline">Use the {overlays.label.signal.strategy_id} signal levels</button>}
            <label className="mt-2 block text-xs text-muted">Thesis (why this trade?)<textarea aria-label="Ticket thesis" className={cx(input, "h-16 font-sans")} value={thesis} onChange={(e) => setThesis(e.target.value)} /></label>

            <div className="mt-3 rounded-md border border-line bg-bg/40 p-3 text-xs" data-testid="ticket-math">
              {math.error ? <p className="text-down">{math.error}</p> : math.riskTicks == null ? <p className="text-faint">Enter a stop to see risk and reward.</p> : (
                <dl className="grid grid-cols-2 gap-y-1 font-mono">
                  <dt className="font-sans text-muted">Risk</dt><dd>{math.riskTicks} ticks · {money(math.riskDollars)}</dd>
                  <dt className="font-sans text-muted">Reward</dt><dd>{math.rewardTicks != null ? `${math.rewardTicks} ticks · ${money(math.rewardDollars)}` : "no target"}</dd>
                  <dt className="font-sans text-muted">Reward:risk</dt><dd className={math.rr != null && math.rr < 1 ? "text-warn" : ""}>{math.rr != null ? math.rr.toFixed(2) : "—"}</dd>
                  <dt className="font-sans text-muted">Reference entry</dt><dd>{refEntry != null ? formatPrice(refEntry, tick) : "—"}{type === "market" ? " (last)" : ""}</dd>
                </dl>
              )}
            </div>
            <button type="button" onClick={() => void doPreview()} className="mt-2 w-full rounded-md border border-line px-3 py-1.5 text-sm text-muted hover:bg-panel-2">Preview against Apex rules</button>
            {preview && (
              <div className="mt-2 text-xs" data-testid="ticket-preview">
                <p className={preview.check.verdict === "violation" ? "text-down" : preview.check.verdict === "cannot_confirm" ? "text-warn" : "text-up"}>{preview.check.summary}</p>
                <p className="text-muted">Loss at stop after costs {money(preview.check.loss_after_costs)} · max size {preview.check.max_size ?? "unknown"} · budget used {preview.check.budget_used_pct ?? "?"}%</p>
                {preview.check.violations.map((v) => <p key={v} className="text-down">✗ {v}</p>)}
                {preview.news.status === "inside_window" && <p className="text-warn">{preview.news.summary}</p>}
              </div>
            )}
            <div className="mt-3 space-y-2">
              <button type="button" disabled={busy || !!math.error || num(stop) == null} onClick={() => void submit("paper")}
                className={cx("w-full rounded-md px-3 py-2 text-sm font-semibold text-bg disabled:opacity-50", side === "long" ? "bg-up hover:bg-up/90" : "bg-down hover:bg-down/90")}>
                {side === "long" ? "BUY" : "SELL"} {qty} {symbol} — paper
              </button>
              <button type="button" disabled={busy || !!math.error || num(stop) == null} onClick={() => void submit("manual")}
                className="w-full rounded-md border border-accent/50 px-3 py-2 text-sm text-accent hover:bg-accent/10 disabled:opacity-50">
                Save plan for manual Tradovate execution
              </button>
              <button type="button" disabled title={live?.reason} data-testid="live-disabled"
                className="w-full cursor-not-allowed rounded-md border border-line px-3 py-2 text-sm text-faint">
                Live order (Tradovate) — disabled
              </button>
              <p className="text-[11px] text-faint">{live?.reason}</p>
            </div>
            {msg && (
              <div role="status" data-testid="ticket-msg" className={cx("mt-3 text-sm", msg.tone === "err" ? "text-down" : msg.tone === "ok" ? "text-up" : "text-accent")}>
                {msg.text}
                {msg.steps && <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-xs text-muted">{msg.steps.map((x) => <li key={x}>{x}</li>)}</ol>}
              </div>
            )}
          </Panel>

          <Panel title="Account risk">
            {paperError && <p className="text-xs text-down">{paperError}</p>}
            {s && (
              <div className="grid grid-cols-2 gap-3" data-testid="paper-risk">
                <Stat label="Paper equity" value={money(s.equity)} hint={`balance ${money(s.balance)}`} />
                <Stat label="Unrealised" value={<span className={s.unrealized >= 0 ? "text-up" : "text-down"}>{money(s.unrealized, true)}</span>} />
                <Stat label="Day P&L" value={money(s.day_pnl, true)} />
                <Stat label="Daily loss left" value={money(s.daily_loss_remaining)} />
                <Stat label="Drawdown room" value={money(s.drawdown_room)} />
                <Stat label="Contracts" value={`${s.contracts_in_use} / ${s.max_contracts}`} />
              </div>
            )}
            <p className="mt-3 border-t border-line pt-2 text-xs text-muted">
              Apex account (your entered figures): risk budget{" "}
              <span className="font-mono text-text">{acct?.state.known ? (acct.state.stale ? "unknown (stale)" : money(budget)) : "unknown"}</span>.{" "}
              <Link href="/account" className="text-accent hover:underline">Update</Link>
            </p>
          </Panel>
          <Panel title="Manual Tradovate workflow">
            <ol className="list-decimal space-y-1 pl-5 text-xs text-muted">{(live?.manual_workflow ?? []).map((x) => <li key={x}>{x}</li>)}</ol>
          </Panel>
        </div>
      </div>
      {event && <NewsPanel event={event} onClose={() => setEvent(null)} />}
    </>
  );
}
