"use client";

import { useEffect, useRef, useState } from "react";
import { cx } from "./primitives";

const pctText = (v: number) => `${Math.round(v * 100)}%`;

/**
 * Semicircle gauge for a 0..1 utilisation (e.g. share of the daily loss limit used).
 * Tone shifts at 50% (warn) and 80% (danger). ``value`` null renders an explicit unknown state.
 */
export function Gauge({ value, label, detail, testId }: { value: number | null; label: string; detail?: string; testId?: string }) {
  const v = value === null ? null : Math.max(0, Math.min(1, value));
  const tone = v === null ? "var(--color-faint)" : v >= 0.8 ? "var(--color-down)" : v >= 0.5 ? "var(--color-warn)" : "var(--color-up)";
  const r = 42, c = Math.PI * r;
  return (
    <figure className="flex flex-col items-center text-center" data-testid={testId} aria-label={`${label}: ${v === null ? "unknown" : pctText(v)}`}>
      <svg viewBox="0 0 100 58" className="w-full max-w-[150px]" role="img" aria-hidden>
        <path d="M8 52 A42 42 0 0 1 92 52" fill="none" stroke="var(--color-line)" strokeWidth="9" strokeLinecap="round" />
        {v !== null && (
          <path d="M8 52 A42 42 0 0 1 92 52" fill="none" stroke={tone} strokeWidth="9" strokeLinecap="round"
            strokeDasharray={`${c * v} ${c}`} className="transition-[stroke-dasharray] duration-500 motion-reduce:transition-none" />
        )}
        <text x="50" y="48" textAnchor="middle" className="fill-text font-mono" fontSize="15">{v === null ? "—" : pctText(v)}</text>
      </svg>
      <figcaption className="mt-1 text-xs font-medium text-text">{label}</figcaption>
      {detail && <p className="text-[11px] text-faint">{detail}</p>}
    </figure>
  );
}

/** Equity line with a drawdown area underneath, from per-trade cumulative P&L. */
export function EquityDrawdown({ points, height = 220 }: { points: { t: string; equity: number; drawdown: number }[]; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  if (points.length < 2) return <p className="text-xs text-faint">At least two closed trades are needed for an equity curve.</p>;
  const W = 600, H = height, top = 12, eqH = H * 0.62, ddTop = eqH + 22, ddH = H - ddTop - 16;
  const eqs = [0, ...points.map((p) => p.equity)];
  const dds = points.map((p) => p.drawdown);
  const minE = Math.min(...eqs), maxE = Math.max(...eqs), minD = Math.min(0, ...dds);
  const x = (i: number) => (i / points.length) * (W - 50) + 4;
  const ye = (v: number) => top + (maxE === minE ? eqH / 2 : ((maxE - v) / (maxE - minE)) * (eqH - top));
  const yd = (v: number) => ddTop + (minD === 0 ? 0 : (v / minD) * ddH);
  const eqPath = eqs.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${ye(v).toFixed(1)}`).join(" ");
  const ddPath = `M${x(1)},${ddTop} ` + dds.map((v, i) => `L${x(i + 1).toFixed(1)},${yd(v).toFixed(1)}`).join(" ") + ` L${x(points.length)},${ddTop} Z`;
  const h = hover !== null ? points[hover] : null;
  const money = (v: number) => `${v < 0 ? "-" : v > 0 ? "+" : ""}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  return (
    <div className="relative" data-testid="equity-chart">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={`Equity curve over ${points.length} trades, final ${money(points.at(-1)!.equity)}, max drawdown ${money(minD)}`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const i = Math.round(((e.clientX - rect.left) / rect.width) * W / ((W - 50) / points.length)) - 1;
          setHover(Math.max(0, Math.min(points.length - 1, i)));
        }}>
        <line x1="0" x2={W - 46} y1={ye(0)} y2={ye(0)} stroke="var(--color-line)" strokeDasharray="3 3" />
        <path d={eqPath} fill="none" stroke="var(--color-accent)" strokeWidth="2" />
        <text x={W - 42} y={ye(maxE) + 4} className="fill-faint" fontSize="10">{money(maxE)}</text>
        <text x={W - 42} y={ye(minE) + 4} className="fill-faint" fontSize="10">{money(minE)}</text>
        <text x="4" y={ddTop - 6} className="fill-faint" fontSize="10">Drawdown from peak</text>
        <path d={ddPath} fill="color-mix(in srgb, var(--color-down) 28%, transparent)" stroke="var(--color-down)" strokeWidth="1" />
        <text x={W - 42} y={ddTop + ddH + 4} className="fill-faint" fontSize="10">{money(minD)}</text>
        {hover !== null && <line x1={x(hover + 1)} x2={x(hover + 1)} y1={top} y2={H - 10} stroke="var(--color-muted)" strokeWidth="0.5" />}
      </svg>
      {h && (
        <div className="pointer-events-none absolute top-1 left-2 rounded border border-line bg-panel/95 px-2 py-1 text-[11px]">
          Trade {hover! + 1}: equity <b className="font-mono">{money(h.equity)}</b> · drawdown <b className="font-mono text-down">{money(h.drawdown)}</b>
        </div>
      )}
    </div>
  );
}

/** Month grid of daily net P&L (trading dates). */
export function PnlCalendar({ days, month, onMonth }: { days: { date: string; net: number; trades: number }[]; month: string; onMonth: (m: string) => void }) {
  const [y, m] = month.split("-").map(Number) as [number, number];
  const first = new Date(Date.UTC(y, m - 1, 1));
  const count = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const lead = (first.getUTCDay() + 6) % 7; // Monday first
  const byDate = new Map(days.map((d) => [d.date, d]));
  const max = Math.max(1, ...days.map((d) => Math.abs(d.net)));
  const shift = (k: number) => {
    const d = new Date(Date.UTC(y, m - 1 + k, 1));
    onMonth(`${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`);
  };
  const monthNet = days.filter((d) => d.date.startsWith(month)).reduce((a, d) => a + d.net, 0);
  return (
    <div data-testid="pnl-calendar">
      <div className="mb-2 flex items-center gap-2 text-sm">
        <button type="button" onClick={() => shift(-1)} aria-label="Previous month" className="rounded border border-line px-2 hover:bg-panel-2">‹</button>
        <span className="font-medium">{first.toLocaleString("en-US", { month: "long", year: "numeric", timeZone: "UTC" })}</span>
        <button type="button" onClick={() => shift(1)} aria-label="Next month" className="rounded border border-line px-2 hover:bg-panel-2">›</button>
        <span className={cx("ml-auto font-mono text-xs", monthNet > 0 ? "text-up" : monthNet < 0 ? "text-down" : "text-faint")}>
          month {monthNet >= 0 ? "+" : "-"}${Math.abs(monthNet).toFixed(2)}
        </span>
      </div>
      <div className="grid grid-cols-7 gap-1 text-center text-[10px] text-faint">
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d}>{d}</div>)}
        {Array.from({ length: lead }, (_, i) => <div key={`l${i}`} />)}
        {Array.from({ length: count }, (_, i) => {
          const date = `${month}-${String(i + 1).padStart(2, "0")}`;
          const d = byDate.get(date);
          const a = d ? 0.15 + 0.55 * (Math.abs(d.net) / max) : 0;
          return (
            <div key={date} title={d ? `${date}: ${d.trades} trade(s), net $${d.net.toFixed(2)}` : date}
              className="flex aspect-square min-h-9 flex-col items-center justify-center rounded border border-line/60"
              style={d ? { background: `color-mix(in srgb, var(--color-${d.net >= 0 ? "up" : "down"}) ${Math.round(a * 100)}%, transparent)` } : undefined}>
              <span className="text-[10px] text-muted">{i + 1}</span>
              {d && <span className="font-mono text-[10px] text-text">{d.net >= 0 ? "+" : "-"}{Math.abs(d.net) >= 1000 ? `${(Math.abs(d.net) / 1000).toFixed(1)}k` : Math.abs(d.net).toFixed(0)}</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const LAYOUT_KEY = "apex.layout";

function readLayout(): Record<string, unknown> {
  try { return JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "{}"); } catch { return {}; }
}

function writeLayout(k: string, v: unknown) {
  try { localStorage.setItem(LAYOUT_KEY, JSON.stringify({ ...readLayout(), [k]: v })); } catch { /* storage unavailable */ }
}

/** A panel that collapses to its header; the state is remembered per browser under ``id``. */
export function CollapsiblePanel({ id, title, actions, children, className }: { id: string; title: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string }) {
  const [open, setOpen] = useState(true);
  useEffect(() => {
    const v = readLayout()[`panel:${id}`];
    // eslint-disable-next-line react-hooks/set-state-in-effect -- restoring the saved layout after mount (SSR-safe)
    if (typeof v === "boolean") setOpen(v);
  }, [id]);
  return (
    <section className={cx("min-w-0 rounded-lg border border-line bg-panel", className)} data-testid={`panel-${id}`}>
      <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
        <button type="button" aria-expanded={open} onClick={() => { setOpen(!open); writeLayout(`panel:${id}`, !open); }}
          className="flex items-center gap-2 text-[13px] font-medium tracking-wide text-muted uppercase hover:text-text">
          <span className={cx("inline-block transition-transform motion-reduce:transition-none", open ? "rotate-90" : "")} aria-hidden>▸</span>
          {title}
        </button>
        {open && actions}
      </header>
      {open && <div className="p-4">{children}</div>}
    </section>
  );
}

/**
 * Two columns with a draggable divider (keyboard: arrow keys on the divider). The right column's
 * width is remembered per browser under ``id``. Below the ``2xl`` breakpoint the columns stack.
 */
export function ResizableSplit({ id, left, right, initial = 380, min = 260, max = 720 }: { id: string; left: React.ReactNode; right: React.ReactNode; initial?: number; min?: number; max?: number }) {
  const [w, setW] = useState(initial);
  const drag = useRef<{ x: number; w: number } | null>(null);
  useEffect(() => {
    const v = readLayout()[`split:${id}`];
    // eslint-disable-next-line react-hooks/set-state-in-effect -- restoring the saved layout after mount (SSR-safe)
    if (typeof v === "number") setW(v);
  }, [id]);
  const set = (v: number) => { const c = Math.max(min, Math.min(max, v)); setW(c); writeLayout(`split:${id}`, c); };
  return (
    <div className="flex flex-col gap-4 2xl:flex-row 2xl:gap-0" style={{ ["--split-w" as string]: `${w}px` }}>
      <div className="min-w-0 flex-1 space-y-4">{left}</div>
      <div role="separator" aria-orientation="vertical" aria-label="Resize side panel" aria-valuenow={w} aria-valuemin={min} aria-valuemax={max} tabIndex={0}
        data-testid={`split-${id}`}
        className="hidden w-4 shrink-0 cursor-col-resize items-center justify-center 2xl:flex group focus:outline-none"
        onKeyDown={(e) => { if (e.key === "ArrowLeft") set(w + 20); if (e.key === "ArrowRight") set(w - 20); }}
        onPointerDown={(e) => { drag.current = { x: e.clientX, w }; (e.target as HTMLElement).setPointerCapture(e.pointerId); }}
        onPointerMove={(e) => { if (drag.current) set(drag.current.w - (e.clientX - drag.current.x)); }}
        onPointerUp={() => { drag.current = null; }}>
        <span className="h-12 w-1 rounded bg-line group-hover:bg-accent group-focus:bg-accent" />
      </div>
      <div className="min-w-0 space-y-4 2xl:w-[var(--split-w)] 2xl:shrink-0">{right}</div>
    </div>
  );
}
