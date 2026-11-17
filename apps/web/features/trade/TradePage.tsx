"use client";

import type { JournalResponse, ReplayData, TradeDetail } from "@apex/shared-types";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, EmptyState, Panel, Stat } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { formatEt } from "@/lib/format";
import { money } from "@/lib/money";
import { entryIndex, replayView } from "@/lib/replay";
import { AnswerBody } from "../copilot/Answer";
import { CandleChart, type ExtraMarker, type PriceLineSpec } from "../charts/CandleChart";

const SEV = { high: "text-down", medium: "text-warn", low: "text-muted" };

export function TradePage() {
  const [id, setId] = useState<number | null>(null);
  const [trade, setTrade] = useState<TradeDetail | null>(null);
  const [list, setList] = useState<JournalResponse | null>(null);
  const [tab, setTab] = useState<"review" | "replay">("review");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const v = Number(q.get("id"));
    // One-time read of the URL after mount (prerendered page).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (v > 0) setId(v);
    if (q.get("tab") === "replay") setTab("replay");
    if (!(v > 0)) api<JournalResponse>("/api/journal/trades?limit=50").then(setList).catch(() => undefined);
  }, []);

  const load = useCallback(async (tid: number) => {
    try {
      setTrade(await api<TradeDetail>(`/api/journal/trades/${tid}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    // load() sets state only after its await resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (id) void load(id);
  }, [id, load]);

  if (!id) {
    return (
      <>
        <PageHeader path="/trade" />
        <Panel title="Choose a trade to review or replay">
          {!list?.trades.length ? <EmptyState title="No trades yet">Close a paper trade or import trades on the Trading Journal.</EmptyState> : (
            <ul className="space-y-1 text-sm" data-testid="trade-pick">
              {list.trades.map((t) => (
                <li key={t.id}><a href={`/trade?id=${t.id}`} className="font-mono text-accent hover:underline">#{t.id} {formatEt(t.exit_time, true)} {t.symbol} {t.direction} {t.quantity}</a></li>
              ))}
            </ul>
          )}
        </Panel>
      </>
    );
  }
  return (
    <>
      <PageHeader path="/trade">
        <Link href="/journal" className="text-xs text-accent hover:underline">← Trading Journal</Link>
      </PageHeader>
      {error && <Callout tone="danger" title="Could not load the trade">{error}</Callout>}
      {trade?.review_error && <div className="mb-3"><Callout tone="warn" title="Review unavailable">{trade.review_error}</Callout></div>}
      {trade && (
        <>
          <Panel>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
              <span className="font-mono text-lg">#{trade.id} {trade.symbol}</span>
              <span className={trade.direction === "long" ? "text-up" : "text-down"}>{trade.direction.toUpperCase()} {trade.quantity}</span>
              <span className="text-muted">{formatEt(trade.entry_time, true)} → {formatEt(trade.exit_time)}</span>
              <span className={cx("rounded border px-1.5 py-px text-[11px]", trade.simulated ? "border-accent/40 text-accent" : "border-line text-muted")}>
                {trade.simulated ? "SIMULATED (paper)" : trade.broker_record ? "Broker record (imported)" : "Manual entry"}
              </span>
              <div role="tablist" className="ml-auto flex overflow-hidden rounded-md border border-line">
                {(["review", "replay"] as const).map((t) => (
                  <button key={t} role="tab" type="button" aria-selected={tab === t} onClick={() => setTab(t)}
                    className={cx("px-3 py-1.5 text-xs", tab === t ? "bg-accent/15 text-accent" : "text-muted hover:bg-panel-2")}>
                    {t === "review" ? "Review (hindsight)" : "Replay (decision view)"}
                  </button>
                ))}
              </div>
            </div>
          </Panel>
          <div className="mt-4">{tab === "review" ? <Review trade={trade} onChange={setTrade} /> : <Replay trade={trade} />}</div>
        </>
      )}
    </>
  );
}

function Review({ trade, onChange }: { trade: TradeDetail; onChange: (t: TradeDetail) => void }) {
  const r = trade.review;
  const [busy, setBusy] = useState(false);
  const [aiErr, setAiErr] = useState<string | null>(null);
  const ai = async () => {
    setBusy(true);
    setAiErr(null);
    try {
      onChange(await api<TradeDetail>(`/api/journal/trades/${trade.id}/ai-review`, { method: "POST" }));
    } catch (e) {
      setAiErr(e instanceof ApiError ? e.message : "AI review failed");
    } finally {
      setBusy(false);
    }
  };
  if (!r) {
    if (!trade.review_error) return <p className="text-sm text-faint">Generating review…</p>;
    return (
      <Panel title="Trade record" className="mb-4">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4" data-testid="trade-record">
          <Stat label="Net P&L" value={<span className={trade.net_pnl >= 0 ? "text-up" : "text-down"}>{money(trade.net_pnl, true)}</span>} hint={`gross ${money(trade.gross_pnl, true)}`} />
          <Stat label="Commission" value={money(trade.fees)} />
          <Stat label="Entry" value={`${trade.entry_price}`} hint={formatEt(trade.entry_time, true)} />
          <Stat label="Exit" value={`${trade.exit_price}`} hint={formatEt(trade.exit_time, true)} />
        </div>
      </Panel>
    );
  }
  const J: [keyof typeof r.journal, string][] = [
    ["trade_summary", "Trade summary"], ["entry_analysis", "Entry analysis"], ["market_conditions", "Market conditions"],
    ["price_action_at_entry", "Price action at entry (5m)"], ["news_context", "News context"], ["risk_analysis", "Risk analysis"], ["execution_review", "Execution review"],
    ["outcome", "Outcome"], ["price_action_after", "Price action after entry (hindsight)"], ["improvement_opportunities", "Improvement opportunities"], ["lesson", "Lesson"],
  ];
  return (
    <div className="space-y-4" data-testid="trade-review">
      <Panel>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-8">
          <Stat label="Net P&L" value={<span className={trade.net_pnl >= 0 ? "text-up" : "text-down"}>{money(trade.net_pnl, true)}</span>} hint={`gross ${money(trade.gross_pnl, true)}`} />
          <Stat label="Commission" value={money(trade.fees)} />
          <Stat label="Slippage" value={money(trade.slippage)} hint="inside fill prices" />
          <Stat label="R multiple" value={trade.context?.r_multiple ?? "—"} />
          <Stat label="MFE" value={money(trade.mfe)} />
          <Stat label="MAE" value={money(trade.mae)} />
          <Stat label="Duration" value={`${Math.round(trade.duration_seconds / 60)} min`} />
          <Stat label="Stop / target" value={<span className="whitespace-normal break-words text-base">{trade.planned_stop ?? "—"} / {trade.planned_target ?? "—"}</span>} />
        </div>
      </Panel>
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Automatic journal">
          <dl className="space-y-2 text-sm" data-testid="journal-sections">
            {J.filter(([k]) => r.journal[k]).map(([k, label]) => (
              <div key={k}><dt className="text-[11px] font-medium tracking-wide text-muted uppercase">{label}</dt><dd className="text-text/90">{r.journal[k]}</dd></div>
            ))}
          </dl>
        </Panel>
        <Panel title="Coach">
          <p className="mb-2 text-xs text-faint">{r.coaching.outcome_note}</p>
          <h3 className="text-xs font-semibold text-up uppercase">What you did well</h3>
          <ul className="mb-3 list-disc pl-5 text-sm">{r.coaching.did_well.map((x) => <li key={x}>{x}</li>)}</ul>
          <h3 className="text-xs font-semibold text-warn uppercase">Mistakes supported by evidence</h3>
          {r.coaching.supported_mistakes.length === 0 ? <p className="mb-3 text-sm text-muted">None found by the rule checks.</p> : (
            <ul className="mb-3 space-y-1.5 text-sm" data-testid="coach-findings">
              {r.coaching.supported_mistakes.map((f) => (
                <li key={f.code}><span className={SEV[f.severity]}>{f.title}</span> <span className="text-muted">— {f.evidence}</span></li>
              ))}
            </ul>
          )}
          <h3 className="text-xs font-semibold text-muted uppercase">After entry</h3>
          <p className="mb-3 text-sm">{r.coaching.after_entry}</p>
          <h3 className="text-xs font-semibold text-accent uppercase">Next learning objective</h3>
          <p className="text-sm">{r.coaching.next_objective}</p>
        </Panel>
        <Panel title="News → market → trade timeline">
          <ol className="space-y-1.5 border-l border-line pl-4 text-sm" data-testid="trade-timeline">
            {r.timeline.map((x, i) => (
              <li key={i} className="relative">
                <span className={cx("absolute -left-[21px] top-1.5 h-2 w-2 rounded-full", x.kind === "news" ? "bg-down" : x.kind === "reaction" ? "bg-warn" : x.kind === "entry" ? "bg-accent" : x.kind === "exit" ? "bg-up" : "bg-faint")} />
                <span className="font-mono text-[11px] text-faint">{formatEt(x.ts, true)}</span> <span className="text-xs uppercase text-muted">{x.kind}</span> {x.text}
              </li>
            ))}
          </ol>
          {(trade.context?.news.events ?? []).map((e) => (
            <p key={e.ts + e.title} className="mt-2 text-xs text-muted"><span className="text-text">{e.title}</span>: {e.description} {e.forecast}</p>
          ))}
          <p className="mt-2 text-[11px] text-faint">{trade.context?.news.note}</p>
        </Panel>
        <Panel title="Alternative scenarios (hypothetical)">
          <table className="w-full text-xs" data-testid="scenarios">
            <thead><tr className="text-left text-faint"><th className="py-1">Scenario</th><th>Hypothetical P&L</th><th>vs actual</th></tr></thead>
            <tbody>{r.scenarios.map((sc) => (
              <tr key={sc.name} className="border-t border-line/60 align-top">
                <td className="py-1.5 pr-2"><span className="text-text">{sc.name}</span><br /><span className="text-faint">{sc.detail}</span></td>
                <td className="font-mono">{money(sc.pnl, true)}</td>
                <td className={cx("font-mono", sc.difference_vs_actual >= 0 ? "text-up" : "text-down")}>{money(sc.difference_vs_actual, true)}</td>
              </tr>
            ))}</tbody>
          </table>
          <p className="mt-2 text-[11px] text-faint">Every result is hypothetical, computed from stored bars with the same costs and conservative fills. The best one in hindsight is not an achievable strategy.</p>
        </Panel>
      </div>
      <Panel title="AI coaching narrative (optional)">
        {r.ai ? (
          <>
            <AnswerBody text={r.ai.text} />
            <p className="mt-2 text-[11px] text-faint">{r.ai.model} · {formatEt(r.ai.generated_at, true)} · {r.ai.grounding}</p>
          </>
        ) : <p className="text-sm text-faint">Ask Claude to write coaching from this record. It sees only the data on this page.</p>}
        <button type="button" disabled={busy} onClick={() => void ai()} className="mt-3 rounded-md border border-accent/50 px-3 py-1.5 text-sm text-accent hover:bg-accent/10 disabled:opacity-50">
          {busy ? "Writing…" : r.ai ? "Regenerate AI review" : "Generate AI review"}
        </button>
        {aiErr && <p className="mt-2 text-sm text-down">{aiErr}</p>}
      </Panel>
    </div>
  );
}

function Replay({ trade }: { trade: TradeDetail }) {
  const [d, setD] = useState<ReplayData | null>(null);
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [showHindsight, setShowHindsight] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  useEffect(() => {
    api<ReplayData>(`/api/journal/trades/${trade.id}/replay`).then((r) => {
      setD(r);
      setI(Math.max(0, entryIndex(r) - 30));
    }).catch(() => undefined);
  }, [trade.id]);
  useEffect(() => {
    if (!playing || !d) return;
    timer.current = setInterval(() => setI((x) => {
      if (x >= d.bars.length - 1) { setPlaying(false); return x; }
      return x + 1;
    }), 1000 / speed);
    return () => clearInterval(timer.current);
  }, [playing, speed, d]);

  const v = useMemo(() => (d ? replayView(d, i) : null), [d, i]);
  const lines = useMemo<PriceLineSpec[]>(() => {
    if (!d || !v?.afterEntry) return [];
    const out: PriceLineSpec[] = [{ price: d.entry.price, title: "entry", color: "#38bdf8" }];
    if (d.stop) out.push({ price: d.stop, title: "original stop", color: "#f05a5e", dashed: true });
    if (d.target) out.push({ price: d.target, title: "original target", color: "#34d399", dashed: true });
    return out;
  }, [d, v?.afterEntry]);
  const markers = useMemo<ExtraMarker[]>(() => {
    if (!d || !v) return [];
    const out: ExtraMarker[] = [];
    const toBar = (t: number) => v.bars.filter((b) => b.t <= t).at(-1)?.t;
    if (v.afterEntry) {
      const t = toBar(d.entry.t);
      if (t) out.push({ t, text: `ENTRY ${d.entry.direction}`, color: "#38bdf8", position: d.entry.direction === "long" ? "belowBar" : "aboveBar", shape: d.entry.direction === "long" ? "arrowUp" : "arrowDown" });
    }
    if (v.exited) {
      const t = toBar(d.exit.t);
      if (t) out.push({ t, text: "EXIT", color: "#e6e8eb", position: "aboveBar", shape: "circle" });
    }
    for (const e of v.pastEvents) {
      const t = toBar(e.t);
      if (t) out.push({ t, text: e.importance === "high" ? "NEWS" : "news", color: e.importance === "high" ? "#f05a5e" : "#f5b84a", position: "aboveBar", shape: "square" });
    }
    return out;
  }, [d, v]);

  if (!d || !v) return <p className="text-sm text-faint">Loading replay…</p>;
  if (d.bars.length === 0) return <EmptyState title="Historical replay data is missing">{d.data_note ?? "Replay needs stored market data for the trade's time window."}</EmptyState>;
  const last = v.bars.at(-1)!;
  const s = d.entry.direction === "long" ? 1 : -1;
  const openPts = v.inTrade ? (last.c - d.entry.price) * s : null;

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_20rem]" data-testid="replay">
      <Panel>
        <CandleChart bars={v.bars} tick={0.25} daily={false} seriesKey={`replay:${d.trade_id}`} lines={lines} markers={markers} height="h-[420px] w-full" />
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <button type="button" aria-label="Step back" onClick={() => setI((x) => Math.max(0, x - 1))} className="rounded border border-line px-2 py-1">◀</button>
          <button type="button" onClick={() => setPlaying((p) => !p)} className="rounded border border-accent/50 px-3 py-1 text-accent" data-testid="replay-play">{playing ? "Pause" : "Play"}</button>
          <button type="button" aria-label="Step forward" onClick={() => setI((x) => Math.min(d.bars.length - 1, x + 1))} className="rounded border border-line px-2 py-1">▶</button>
          <select aria-label="Replay speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} className="rounded border border-line bg-panel-2 px-2 py-1 text-xs">
            {[1, 4, 10, 30].map((x) => <option key={x} value={x}>{x} bars/s</option>)}
          </select>
          <input type="range" aria-label="Replay position" min={0} max={d.bars.length - 1} value={i} onChange={(e) => setI(Number(e.target.value))} className="min-w-40 flex-1 accent-sky-400" />
          <span className="font-mono text-xs text-muted" data-testid="replay-clock">{formatEt(new Date(v.now * 1000), true)}</span>
        </div>
        <p className="mt-2 text-[11px] text-faint">Decision view: only bars that had closed and events that were known at the replay time are shown. Future candles and news stay hidden.</p>
      </Panel>
      <div className="space-y-4">
        <Panel title="Known at this moment">
          <dl className="space-y-1 text-xs">
            <div className="flex justify-between"><dt className="text-muted">Last close</dt><dd className="font-mono">{last.c}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Position</dt><dd className="font-mono" data-testid="replay-position">{v.inTrade ? `${d.entry.direction} @ ${d.entry.price}` : v.afterEntry ? "closed" : "none yet"}</dd></div>
            {openPts != null && <div className="flex justify-between"><dt className="text-muted">Open P&L (points)</dt><dd className={cx("font-mono", openPts >= 0 ? "text-up" : "text-down")}>{openPts.toFixed(2)}</dd></div>}
          </dl>
          {v.signal && <p className="mt-2 text-xs text-muted">Signal at entry: <span className="text-text">{String((v.signal as { label?: string }).label ?? "")} {String((v.signal as { strategy_id?: string }).strategy_id ?? "")}</span></p>}
          {v.market && <p className="mt-1 text-xs text-muted">Regime at entry: <span className="text-text">{String((v.market as { regime?: string }).regime ?? "n/a")}</span></p>}
          <p className="mt-2 text-xs font-medium text-muted">Events known so far</p>
          <ul className="text-xs" data-testid="replay-events">
            {v.knownEvents.length === 0 ? <li className="text-faint">none</li> : v.knownEvents.map((e) => <li key={e.title + e.t}>{formatEt(new Date(e.t * 1000))} {e.title} ({e.importance})</li>)}
          </ul>
        </Panel>
        <Panel title="Hindsight analysis">
          {!v.finished && !showHindsight ? (
            <div className="text-xs text-muted">
              <p>Hidden until the replay reaches the end, so the outcome does not influence how you judge the decision.</p>
              <button type="button" disabled className="mt-2 cursor-not-allowed rounded border border-line px-2 py-1 text-faint">Locked during replay</button>
            </div>
          ) : (
            <div className="text-xs" data-testid="hindsight">
              <p>Realised: <span className={cx("font-mono", d.net_pnl >= 0 ? "text-up" : "text-down")}>{money(d.net_pnl, true)}</span></p>
              <ul className="mt-2 list-disc space-y-0.5 pl-4">{(d.hindsight?.supported_mistakes ?? []).map((f) => <li key={f.code}>{f.title}</li>)}</ul>
              <p className="mt-2 text-muted">{d.hindsight?.next_objective}</p>
            </div>
          )}
          {v.finished && !showHindsight && <button type="button" onClick={() => setShowHindsight(true)} className="mt-2 text-xs text-accent hover:underline">Pin hindsight open</button>}
        </Panel>
      </div>
    </div>
  );
}
