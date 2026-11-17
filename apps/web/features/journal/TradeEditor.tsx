"use client";

import type { DeletedTrade, JournalAttachment, JournalHistoryItem, JournalNote, JournalTrade } from "@apex/shared-types";
import { useCallback, useEffect, useState } from "react";
import { Callout, cx, Panel } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { formatEt, MARKET_TZ } from "@/lib/format";
import { money } from "@/lib/money";

const SYMBOLS = ["ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K", "CL", "MCL", "GC", "MGC"];
const MISTAKES = ["chased_entry", "moved_stop", "no_stop", "early_exit", "oversized", "revenge_trade", "traded_news", "outside_plan", "late_exit", "fomo"];
const STRATEGIES = ["orb", "vwap_trend", "sweep_reversal", "breakout_retest", "momentum_pullback", "vol_expansion", "mean_reversion", "mtf_trend", "smc_sweep_mss", "smc_ob_retest", "smc_fvg_retest", "discretionary"];
const input = "w-full rounded-md border border-line bg-panel-2 px-2.5 py-1.5 text-sm outline-none focus:border-accent";

/** ISO UTC -> "YYYY-MM-DDTHH:MM" in New York wall time for <input type=datetime-local>. */
export function toNyLocal(iso: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: MARKET_TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).formatToParts(new Date(iso));
  const g = (t: string) => parts.find((p) => p.type === t)?.value ?? "00";
  return `${g("year")}-${g("month")}-${g("day")}T${g("hour") === "24" ? "00" : g("hour")}:${g("minute")}:${g("second")}`;
}

/** New York wall time "YYYY-MM-DDTHH:MM[:SS]" -> ISO UTC, resolving the DST offset for that date. */
export function fromNyLocal(local: string): string {
  const [d, t] = local.split("T");
  const [y, mo, da] = d!.split("-").map(Number) as [number, number, number];
  const [h, mi, se] = (t ?? "00:00").split(":").map(Number) as [number, number, number?];
  const guess = Date.UTC(y, mo - 1, da, h, mi, se ?? 0);
  // Offset of New York at that instant (try twice to settle across DST changes).
  let ts = guess;
  for (let i = 0; i < 2; i++) {
    const back = toNyLocal(new Date(ts).toISOString());
    const [bd, bt] = back.split("T");
    const [by, bmo, bda] = bd!.split("-").map(Number) as [number, number, number];
    const [bh, bmi, bse] = bt!.split(":").map(Number) as [number, number, number];
    ts += guess - Date.UTC(by, bmo - 1, bda, bh, bmi, bse);
  }
  return new Date(ts).toISOString();
}

interface Form {
  trading_date: string; entry_time: string; exit_time: string; symbol: string; contract: string; direction: string;
  quantity: string; entry_price: string; exit_price: string; fees: string; net_pnl: string;
  planned_stop: string; planned_target: string; strategy_id: string; setup: string; followed_plan: string;
  thesis: string; psychology: string; emotion_tags: string; lessons: string; tags: string; chart_links: string;
  mistakes: string[]; notes: string;
}

function toForm(t: JournalTrade): Form {
  return {
    trading_date: t.trading_date, entry_time: toNyLocal(t.entry_time), exit_time: toNyLocal(t.exit_time), symbol: t.symbol,
    contract: t.contract ?? "", direction: t.direction, quantity: String(t.quantity), entry_price: String(t.entry_price),
    exit_price: String(t.exit_price), fees: String(t.fees), net_pnl: String(t.net_pnl),
    planned_stop: t.planned_stop?.toString() ?? "", planned_target: t.planned_target?.toString() ?? "",
    strategy_id: t.strategy_id ?? "", setup: t.setup ?? "", followed_plan: t.followed_plan == null ? "" : String(t.followed_plan),
    thesis: t.thesis ?? "", psychology: t.psychology ?? "", emotion_tags: (t.emotion_tags ?? []).join(", "), lessons: t.lessons ?? "",
    tags: (t.tags ?? []).join(", "), chart_links: (t.chart_links ?? []).join("\n"), mistakes: t.mistakes, notes: t.notes ?? "",
  };
}

/** Only fields that differ from the trade are sent, so untouched values are never re-recorded. */
export function diff(t: JournalTrade, f: Form): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const num = (v: string) => (v.trim() === "" ? null : Number(v));
  const list = (v: string, sep: RegExp) => v.split(sep).map((x) => x.trim()).filter(Boolean);
  const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
  const set = (k: string, now: unknown, was: unknown) => { if (!same(now, was)) out[k] = now; };
  set("trading_date", f.trading_date, t.trading_date);
  if (f.entry_time !== toNyLocal(t.entry_time)) out.entry_time = fromNyLocal(f.entry_time);
  if (f.exit_time !== toNyLocal(t.exit_time)) out.exit_time = fromNyLocal(f.exit_time);
  set("symbol", f.symbol, t.symbol);
  set("contract", f.contract || null, t.contract);
  set("direction", f.direction, t.direction);
  set("quantity", num(f.quantity), t.quantity);
  set("entry_price", num(f.entry_price), t.entry_price);
  set("exit_price", num(f.exit_price), t.exit_price);
  set("fees", num(f.fees), t.fees);
  // Realised P&L is sent only when the user typed a different value than the stored one.
  set("net_pnl", num(f.net_pnl), t.net_pnl);
  set("planned_stop", num(f.planned_stop), t.planned_stop);
  set("planned_target", num(f.planned_target), t.planned_target);
  set("strategy_id", f.strategy_id || null, t.strategy_id);
  set("setup", f.setup || null, t.setup ?? null);
  set("followed_plan", f.followed_plan === "" ? null : f.followed_plan === "true", t.followed_plan);
  set("thesis", f.thesis || null, t.thesis ?? null);
  set("psychology", f.psychology || null, t.psychology ?? null);
  set("emotion_tags", [...new Set(list(f.emotion_tags, /,/))].sort(), t.emotion_tags ?? []);
  set("lessons", f.lessons || null, t.lessons ?? null);
  set("tags", [...new Set(list(f.tags, /,/))].sort(), t.tags ?? []);
  set("chart_links", list(f.chart_links, /\n/), t.chart_links ?? []);
  set("mistakes", [...f.mistakes].sort(), [...t.mistakes].sort());
  set("notes", f.notes || null, t.notes);
  // If price/quantity/direction/symbol changed, let the server recompute P&L instead of sending the old one.
  if (["entry_price", "exit_price", "quantity", "direction", "symbol"].some((k) => k in out) && out.net_pnl === undefined) delete out.net_pnl;
  return out;
}

export function TradeEditor({ trade, onClose, onSaved }: { trade: JournalTrade; onClose: () => void; onSaved: () => void }) {
  const [t, setT] = useState(trade);
  const [f, setF] = useState<Form>(() => toForm(trade));
  const [msg, setMsg] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const [notes, setNotes] = useState<JournalNote[]>([]);
  const [atts, setAtts] = useState<JournalAttachment[]>([]);
  const [hist, setHist] = useState<JournalHistoryItem[]>([]);
  const [newNote, setNewNote] = useState("");
  const [confirmDel, setConfirmDel] = useState("");
  const loadSide = useCallback(async () => {
    const [n, a, h] = await Promise.all([
      api<JournalNote[]>(`/api/journal/trades/${trade.id}/notes`),
      api<JournalAttachment[]>(`/api/journal/trades/${trade.id}/attachments`),
      api<JournalHistoryItem[]>(`/api/journal/trades/${trade.id}/history`),
    ]);
    setNotes(n); setAtts(a); setHist(h);
  }, [trade.id]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads sub-resources after mount
    void loadSide().catch(() => undefined);
  }, [loadSide]);
  const upd = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });

  const save = async () => {
    const body = diff(t, f);
    if (!Object.keys(body).length) { setMsg({ tone: "ok", text: "No changes to save." }); return; }
    try {
      const r = await api<JournalTrade & { changes: { field: string }[] }>(`/api/journal/trades/${t.id}`, { method: "PATCH", body });
      setT(r); setF(toForm(r));
      setMsg({ tone: "ok", text: `Saved ${r.changes.length} change(s).` });
      onSaved();
      void loadSide();
    } catch (e) {
      setMsg({ tone: "err", text: e instanceof ApiError ? e.message : "Save failed" });
    }
  };
  const addNote = async () => {
    if (!newNote.trim()) return;
    await api(`/api/journal/trades/${t.id}/notes`, { method: "POST", body: { body: newNote } });
    setNewNote(""); void loadSide();
  };
  const upload = async (file: File | undefined) => {
    if (!file) return;
    const buf = new Uint8Array(await file.arrayBuffer());
    let bin = ""; for (const b of buf) bin += String.fromCharCode(b);
    try {
      await api(`/api/journal/trades/${t.id}/attachments`, { method: "POST", body: { filename: file.name, content_type: file.type, data_base64: btoa(bin) } });
      void loadSide();
    } catch (e) {
      setMsg({ tone: "err", text: e instanceof ApiError ? e.message : "Upload failed" });
    }
  };
  const del = async () => {
    try {
      await api(`/api/journal/trades/${t.id}?confirm=${t.id}`, { method: "DELETE" });
      onSaved(); onClose();
    } catch (e) {
      setMsg({ tone: "err", text: e instanceof ApiError ? e.message : "Delete failed" });
    }
  };

  const field = (label: string, k: keyof Form, props: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <label className="text-xs text-muted">{label}
      <input aria-label={`Edit ${label.toLowerCase()}`} className={cx(input, t.corrected_fields?.includes(k) && "border-warn/60")} value={f[k] as string} onChange={upd(k)} {...props} />
    </label>
  );
  const area = (label: string, k: keyof Form, rows = 3) => (
    <label className="block text-xs text-muted">{label}
      <textarea aria-label={`Edit ${label.toLowerCase()}`} rows={rows} className={input} value={f[k] as string} onChange={upd(k)} />
    </label>
  );

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" onMouseDown={onClose}>
      <aside role="dialog" aria-modal="true" aria-label={`Edit trade #${t.id}`} data-testid="trade-editor"
        className="h-full w-full max-w-3xl overflow-y-auto border-l border-line bg-panel p-5 shadow-2xl motion-safe:animate-[slidein_.18s_ease-out]"
        onMouseDown={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold">Edit trade #{t.id} · {t.symbol} {t.direction} {t.quantity}</h2>
            <p className="text-xs text-faint" data-testid="record-provenance">{t.record_provenance} · times in New York (ET)</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close editor" className="text-faint hover:text-text">✕</button>
        </div>
        {(t.corrected_fields?.length ?? 0) > 0 && t.original && (
          <div className="mb-3">
            <Callout tone="warn" title="Manually corrected record">
              Corrected: {t.corrected_fields!.join(", ")}. Original values are preserved:{" "}
              <span className="font-mono" data-testid="original-values">{Object.entries(t.original).filter(([k]) => t.corrected_fields!.includes(k)).map(([k, v]) => `${k}=${String(v)}`).join(" · ")}</span>
            </Callout>
          </div>
        )}
        <div className="space-y-4">
          <Panel title="Execution (corrections are tracked)">
            <div className="grid gap-3 sm:grid-cols-3">
              {field("Trading date", "trading_date", { type: "date" })}
              {field("Entry time (ET)", "entry_time", { type: "datetime-local", step: 1 })}
              {field("Exit time (ET)", "exit_time", { type: "datetime-local", step: 1 })}
              <label className="text-xs text-muted">Symbol
                <select aria-label="Edit symbol" className={input} value={f.symbol} onChange={upd("symbol")}>{SYMBOLS.map((s) => <option key={s}>{s}</option>)}</select>
              </label>
              {field("Contract", "contract")}
              <label className="text-xs text-muted">Direction
                <select aria-label="Edit direction" className={input} value={f.direction} onChange={upd("direction")}><option value="long">long</option><option value="short">short</option></select>
              </label>
              {field("Quantity", "quantity", { inputMode: "numeric" })}
              {field("Entry price", "entry_price", { inputMode: "decimal" })}
              {field("Exit price", "exit_price", { inputMode: "decimal" })}
              {field("Fees and commissions", "fees", { inputMode: "decimal" })}
              {field("Realised P&L (net)", "net_pnl", { inputMode: "decimal" })}
              <p className="self-end text-[11px] text-faint">Gross {money(t.gross_pnl, true)}. Changing prices, size or direction recomputes P&L.</p>
            </div>
          </Panel>
          <Panel title="Plan, setup and classification">
            <div className="grid gap-3 sm:grid-cols-3">
              {field("Stop-loss", "planned_stop", { inputMode: "decimal" })}
              {field("Take-profit", "planned_target", { inputMode: "decimal" })}
              <label className="text-xs text-muted">Followed plan?
                <select aria-label="Edit followed plan" className={input} value={f.followed_plan} onChange={upd("followed_plan")}><option value="">Not recorded</option><option value="true">Yes</option><option value="false">No</option></select>
              </label>
              <label className="text-xs text-muted">Strategy
                <select aria-label="Edit strategy" className={input} value={f.strategy_id} onChange={upd("strategy_id")}><option value="">Untagged</option>{STRATEGIES.map((s) => <option key={s}>{s}</option>)}</select>
              </label>
              {field("Setup", "setup")}
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">{area("Thesis and entry reasoning", "thesis")}{area("Lessons learned", "lessons")}</div>
          </Panel>
          <Panel title="Psychology, mistakes and tags">
            <div className="grid gap-3 sm:grid-cols-2">
              {area("Emotions and psychology", "psychology")}
              <div className="space-y-3">
                {field("Emotion tags (comma separated)", "emotion_tags")}
                {field("Tags (comma separated)", "tags")}
              </div>
            </div>
            <fieldset className="mt-3"><legend className="text-xs text-muted">Mistakes</legend>
              <div className="mt-1 flex flex-wrap gap-2">
                {MISTAKES.map((m) => (
                  <label key={m} className={cx("cursor-pointer rounded border px-2 py-0.5 text-xs", f.mistakes.includes(m) ? "border-warn/60 text-warn" : "border-line text-muted")}>
                    <input type="checkbox" className="sr-only" checked={f.mistakes.includes(m)} onChange={(e) => setF({ ...f, mistakes: e.target.checked ? [...f.mistakes, m] : f.mistakes.filter((x) => x !== m) })} />
                    {f.mistakes.includes(m) ? "✓ " : ""}{m.replaceAll("_", " ")}
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">{area("Chart references (one URL per line)", "chart_links", 2)}{area("Summary note", "notes", 2)}</div>
          </Panel>
          <div className="sticky bottom-0 flex flex-wrap items-center gap-3 border-t border-line bg-panel py-3">
            <button type="button" onClick={() => void save()} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-bg">Save Changes</button>
            <button type="button" onClick={() => { setF(toForm(t)); setMsg(null); }} className="rounded-md border border-line px-3 py-1.5 text-sm">Discard</button>
            {msg && <span role="status" className={cx("text-sm", msg.tone === "ok" ? "text-up" : "text-down")}>{msg.text}</span>}
          </div>
          <Panel title={`Personal notes (${notes.length})`}>
            <ul className="space-y-2" data-testid="note-list">{notes.map((n) => <NoteRow key={n.id} n={n} onChange={() => void loadSide()} />)}</ul>
            <div className="mt-2 flex gap-2">
              <textarea aria-label="New note" rows={2} className={input} value={newNote} onChange={(e) => setNewNote(e.target.value)} placeholder="Add a note…" />
              <button type="button" onClick={() => void addNote()} className="self-start rounded-md border border-line px-3 py-1.5 text-sm hover:border-accent">Add note</button>
            </div>
          </Panel>
          <Panel title={`Screenshots (${atts.length})`}>
            <div className="flex flex-wrap gap-3" data-testid="attachments">
              {atts.map((a) => (
                <figure key={a.id} className="w-40 text-[11px]">
                  {/* eslint-disable-next-line @next/next/no-img-element -- authenticated API image */}
                  <a href={a.url} target="_blank" rel="noreferrer"><img src={a.url} alt={a.caption ?? a.filename} className="h-24 w-40 rounded border border-line object-cover" /></a>
                  <figcaption className="flex justify-between gap-1 text-faint"><span className="truncate">{a.filename}</span>
                    <button type="button" className="text-down" aria-label={`Delete ${a.filename}`} onClick={() => { if (window.confirm(`Delete screenshot ${a.filename}?`)) void api(a.url, { method: "DELETE" }).then(loadSide); }}>✕</button>
                  </figcaption>
                </figure>
              ))}
            </div>
            <input type="file" accept="image/png,image/jpeg,image/webp" aria-label="Upload screenshot" className="mt-2 text-xs" onChange={(e) => void upload(e.target.files?.[0])} />
            <p className="text-[11px] text-faint">PNG, JPEG or WebP up to 5 MB, stored in the PROJECT APEX database.</p>
          </Panel>
          <Panel title={`Change history (${hist.length})`}>
            <ul className="max-h-48 space-y-0.5 overflow-y-auto font-mono text-[11px]" data-testid="history">
              {hist.map((h) => (
                <li key={h.id}>{formatEt(h.at, true)} · {h.user ?? "?"} · {h.action}{h.field ? ` ${h.field}` : ""}{h.action !== "delete" ? `: ${JSON.stringify(h.old)} → ${JSON.stringify(h.new)}` : ""}</li>
              ))}
            </ul>
          </Panel>
          <Panel title="Delete trade">
            <p className="mb-2 text-xs text-muted">Deletion removes the trade from the journal and analytics. A full copy (without screenshot files) is kept in the change history and can be restored from the Journal page.</p>
            <div className="flex flex-wrap items-center gap-2">
              <input aria-label="Type the trade id to confirm deletion" className={cx(input, "w-40")} placeholder={`Type ${t.id}`} value={confirmDel} onChange={(e) => setConfirmDel(e.target.value)} />
              <button type="button" disabled={confirmDel !== String(t.id)} onClick={() => void del()} className="rounded-md border border-down/60 px-3 py-1.5 text-sm text-down disabled:opacity-40">Delete trade #{t.id}</button>
            </div>
          </Panel>
        </div>
      </aside>
    </div>
  );
}

function NoteRow({ n, onChange }: { n: JournalNote; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(n.body);
  return (
    <li className="rounded border border-line/60 p-2 text-sm">
      {editing ? (
        <div className="flex gap-2">
          <textarea aria-label="Edit note text" rows={2} className={input} value={text} onChange={(e) => setText(e.target.value)} />
          <button type="button" className="self-start text-xs text-accent" onClick={() => void api(`/api/journal/notes/${n.id}`, { method: "PATCH", body: { body: text } }).then(() => { setEditing(false); onChange(); })}>Save note</button>
        </div>
      ) : <p className="whitespace-pre-wrap">{n.body}</p>}
      <p className="mt-1 flex gap-3 text-[11px] text-faint">
        {formatEt(n.created_at, true)}{n.updated_at ? ` · edited ${formatEt(n.updated_at, true)}` : ""}
        <button type="button" className="text-accent" onClick={() => setEditing(!editing)}>{editing ? "Cancel" : "Edit"}</button>
        <button type="button" className="text-down" onClick={() => { if (window.confirm("Delete this note?")) void api(`/api/journal/notes/${n.id}`, { method: "DELETE" }).then(onChange); }}>Delete</button>
      </p>
    </li>
  );
}

/** Recently deleted trades with restore. */
export function DeletedTrades({ onRestored }: { onRestored: () => void }) {
  const [rows, setRows] = useState<DeletedTrade[] | null>(null);
  const load = useCallback(() => api<DeletedTrade[]>("/api/journal/deleted").then(setRows).catch(() => setRows([])), []);
  useEffect(() => {
    void load();
  }, [load]);
  if (!rows?.length) return null;
  return (
    <Panel title="Recently deleted">
      <ul className="space-y-1 text-xs" data-testid="deleted-trades">
        {rows.map((r) => (
          <li key={r.edit_id} className="flex items-center justify-between gap-2">
            <span className="font-mono">#{r.trade_id} {r.symbol} {r.direction} {money(r.net_pnl, true)} · deleted {formatEt(r.deleted_at, true)}</span>
            <button type="button" className="text-accent" onClick={() => void api(`/api/journal/deleted/${r.edit_id}/restore`, { method: "POST" }).then(() => { void load(); onRestored(); })}>Restore</button>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
