"use client";

import { useMemo, useState } from "react";
import { Panel } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { etLocalToIso, type FillRow, previewPnl } from "@/lib/journal_calc";
import { money } from "@/lib/money";

const input = "w-full min-w-0 rounded-md border border-line bg-panel-2 px-2 py-1 text-sm outline-none focus:border-accent";
const STRATEGIES = ["", "mtf_trend", "vwap_trend", "momentum_pullback", "orb", "breakout_retest", "vol_expansion", "sweep_reversal", "mean_reversion", "smc_sweep_mss", "smc_ob_retest", "smc_fvg_retest", "discretionary"];
const MISTAKES = ["early entry", "late entry", "moved stop", "no stop", "oversized", "chased", "ignored news", "revenge trade", "exited early", "held loser"];

interface Row { time: string; side: "entry" | "exit"; qty: string; price: string }

/** Record a trade executed on your own platform. Supports scaled entries and partial exits. */
export function RecordTrade({ onSaved }: { onSaved: () => void }) {
  const [symbol, setSymbol] = useState("ES");
  const [direction, setDirection] = useState<"long" | "short">("long");
  const [rows, setRows] = useState<Row[]>([{ time: "", side: "entry", qty: "1", price: "" }, { time: "", side: "exit", qty: "1", price: "" }]);
  const [fees, setFees] = useState("");
  const [stop, setStop] = useState("");
  const [target, setTarget] = useState("");
  const [contract, setContract] = useState("");
  const [strategy, setStrategy] = useState("");
  const [tags, setTags] = useState("");
  const [mistakes, setMistakes] = useState<string[]>([]);
  const [emotion, setEmotion] = useState("");
  const [notes, setNotes] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const fills: (FillRow & { iso: string | null })[] = rows.map((r) => ({ time: r.time, iso: etLocalToIso(r.time), side: r.side, qty: Number(r.qty) || 0, price: Number(r.price) || 0 }));
  const feeNum = fees === "" ? 0 : Number(fees);
  const p = useMemo(() => previewPnl(symbol, direction, fills, feeNum, stop ? Number(stop) : null), [symbol, direction, fills, feeNum, stop]);
  const set = (i: number, k: keyof Row, v: string) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [k]: v } : r)));

  const save = async () => {
    setMsg(null);
    if (fills.some((f) => !f.iso)) { setMsg({ ok: false, text: "Every fill needs a date and time (Eastern)." }); return; }
    if (!p.balanced) { setMsg({ ok: false, text: p.error ?? "Check the fills." }); return; }
    setBusy(true);
    try {
      await api("/api/journal/trades", { method: "POST", body: {
        symbol, direction,
        fills: fills.map((f) => ({ time: f.iso, side: f.side, qty: f.qty, price: f.price })),
        ...(fees !== "" ? { fees: feeNum } : {}),
        ...(stop ? { planned_stop: Number(stop) } : {}),
        ...(target ? { planned_target: Number(target) } : {}),
        ...(contract ? { contract } : {}),
        ...(strategy ? { strategy_id: strategy } : {}),
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
        mistakes,
        ...(emotion ? { psychology: emotion } : {}),
        ...(notes ? { notes } : {}),
      } });
      setMsg({ ok: true, text: "Trade recorded (MANUALLY ENTERED)." });
      setRows([{ time: "", side: "entry", qty: "1", price: "" }, { time: "", side: "exit", qty: "1", price: "" }]);
      setNotes(""); setMistakes([]); setEmotion("");
      onSaved();
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Record a trade">
      <div className="space-y-2 text-xs" data-testid="record-trade">
        <div className="grid grid-cols-3 gap-2">
          <label>Symbol<select aria-label="Symbol" className={input} value={symbol} onChange={(e) => setSymbol(e.target.value)}>{["ES", "NQ", "MNQ", "MES"].map((s) => <option key={s}>{s}</option>)}</select></label>
          <label>Side<select aria-label="Side" className={input} value={direction} onChange={(e) => setDirection(e.target.value as "long" | "short")}><option value="long">Long</option><option value="short">Short</option></select></label>
          <label>Contract<input aria-label="Contract" className={input} placeholder="ESZ6" value={contract} onChange={(e) => setContract(e.target.value)} /></label>
        </div>
        <p className="text-faint">Fills (Eastern time). Add rows for scaled entries or partial exits.</p>
        {rows.map((r, i) => (
          <div key={i} className="grid grid-cols-[4.5rem_minmax(0,1fr)_2.75rem_4.75rem_1rem] items-center gap-1">
            <select aria-label={`Fill ${i + 1} side`} className={input} value={r.side} onChange={(e) => set(i, "side", e.target.value)}><option value="entry">Entry</option><option value="exit">Exit</option></select>
            <input aria-label={`Fill ${i + 1} time`} type="datetime-local" className={input} value={r.time} onChange={(e) => set(i, "time", e.target.value)} />
            <input aria-label={`Fill ${i + 1} quantity`} inputMode="numeric" className={input} value={r.qty} onChange={(e) => set(i, "qty", e.target.value)} />
            <input aria-label={`Fill ${i + 1} price`} inputMode="decimal" className={input} placeholder="price" value={r.price} onChange={(e) => set(i, "price", e.target.value)} />
            <button type="button" aria-label={`Remove fill ${i + 1}`} className="text-faint hover:text-down" disabled={rows.length <= 2} onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}>×</button>
          </div>
        ))}
        <div className="flex gap-2">
          <button type="button" className="rounded border border-line px-2 py-0.5 text-muted hover:text-text" onClick={() => setRows((rs) => [...rs, { time: "", side: "entry", qty: "1", price: "" }])}>+ entry</button>
          <button type="button" className="rounded border border-line px-2 py-0.5 text-muted hover:text-text" onClick={() => setRows((rs) => [...rs, { time: "", side: "exit", qty: "1", price: "" }])}>+ exit</button>
        </div>
        <div className="grid grid-cols-3 gap-2">
          <label>Fees ($)<input aria-label="Fees" inputMode="decimal" className={input} placeholder="auto" value={fees} onChange={(e) => setFees(e.target.value)} /></label>
          <label>Stop-loss<input aria-label="Stop-loss" inputMode="decimal" className={input} value={stop} onChange={(e) => setStop(e.target.value)} /></label>
          <label>Target<input aria-label="Target" inputMode="decimal" className={input} value={target} onChange={(e) => setTarget(e.target.value)} /></label>
        </div>
        <label className="block">Strategy<select aria-label="Strategy" className={input} value={strategy} onChange={(e) => setStrategy(e.target.value)}>{STRATEGIES.map((s) => <option key={s} value={s}>{s || "—"}</option>)}</select></label>
        <label className="block">Setup tags (comma separated)<input aria-label="Setup tags" className={input} value={tags} onChange={(e) => setTags(e.target.value)} /></label>
        <fieldset>
          <legend className="mb-1">Mistakes</legend>
          <div className="flex flex-wrap gap-1">
            {MISTAKES.map((m) => (
              <button key={m} type="button" onClick={() => setMistakes((ms) => (ms.includes(m) ? ms.filter((x) => x !== m) : [...ms, m]))}
                className={mistakes.includes(m) ? "rounded border border-warn/60 bg-warn/10 px-1.5 py-0.5 text-warn" : "rounded border border-line px-1.5 py-0.5 text-muted"}>{m}</button>
            ))}
          </div>
        </fieldset>
        <label className="block">Emotion / discipline<input aria-label="Emotion" className={input} value={emotion} onChange={(e) => setEmotion(e.target.value)} /></label>
        <label className="block">Notes<textarea aria-label="Notes" className={input} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
        <div className="rounded border border-line bg-panel-2 p-2 font-mono" data-testid="pnl-preview">
          {p.balanced ? (
            <>
              <p>{p.qty} × avg {p.avgEntry?.toFixed(2)} → {p.avgExit?.toFixed(2)} ({p.points} pts)</p>
              <p>Gross {money(p.gross, true)} · fees {fees === "" ? "auto" : money(feeNum)} · net {fees === "" ? "after auto fees" : money(p.net, true)}{p.r != null && fees !== "" ? ` · ${p.r}R` : ""}</p>
            </>
          ) : <p className="text-faint">{p.error}</p>}
        </div>
        <button type="button" disabled={busy} onClick={() => void save()} className="w-full rounded-md bg-accent/90 px-3 py-1.5 text-sm font-semibold text-bg hover:bg-accent disabled:opacity-50">Save trade</button>
        {msg && <p className={msg.ok ? "text-up" : "text-down"}>{msg.text}</p>}
        <p className="text-[11px] text-faint">Screenshots can be attached from the trade row after saving. Entries are labelled MANUALLY ENTERED.</p>
      </div>
    </Panel>
  );
}
