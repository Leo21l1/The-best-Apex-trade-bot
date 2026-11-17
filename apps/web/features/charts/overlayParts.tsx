"use client";

import type { ChartEvent, Overlays, Timeframe, TradeLabel } from "@apex/shared-types";
import { useState } from "react";
import { cx } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { formatEt } from "@/lib/format";
import type { OverlayToggles } from "./CandleChart";

export function useOverlays(symbol: string, tf: Timeframe) {
  return usePolling<Overlays>(`/api/market/overlays?symbol=${symbol}&timeframe=${tf}&limit=600`, 20_000).data;
}

const LABEL_STYLE: Record<TradeLabel["label"], string> = {
  BUY: "border-up/50 bg-up/10 text-up",
  SHORT: "border-down/50 bg-down/10 text-down",
  WAIT: "border-line bg-panel-2 text-muted",
  "NO TRADE": "border-warn/50 bg-warn/10 text-warn",
};

/** BUY / SHORT / WAIT / NO TRADE with the reasons always one click away. */
export function SignalBadge({ label }: { label: TradeLabel | undefined }) {
  const [open, setOpen] = useState(false);
  if (!label) return null;
  return (
    <span className="relative">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} data-testid="signal-badge"
        className={cx("rounded border px-2 py-0.5 font-mono text-xs font-semibold tracking-wider transition-colors", LABEL_STYLE[label.label])}
        title="Why this label?">
        {label.label}
      </button>
      {open && (
        <span role="tooltip" className="absolute right-0 z-30 mt-2 block w-80 rounded-md border border-line bg-panel p-3 text-left text-xs text-muted shadow-2xl">
          <span className="mb-1 block font-medium text-text">Why {label.label}?</span>
          {label.reasons.map((r) => <span key={r} className="mb-1 block">• {r}</span>)}
          <span className="mt-2 block text-faint">Analysis only. Labels come from strategy rules and the event calendar; they are not trade instructions.</span>
        </span>
      )}
    </span>
  );
}

export function OverlayToggleBar({ value, onChange }: { value: OverlayToggles; onChange: (v: OverlayToggles) => void }) {
  const items: [keyof OverlayToggles, string, string][] = [
    ["ema", "EMA 20/50", "text-accent"], ["vwap", "VWAP", "text-warn"], ["levels", "Levels", "text-muted"],
    ["signals", "Signals", "text-up"], ["news", "News", "text-down"],
  ];
  return (
    <div role="group" aria-label="Chart overlays" className="flex flex-wrap gap-1">
      {items.map(([k, label, color]) => (
        <button key={k} type="button" aria-pressed={value[k]} onClick={() => onChange({ ...value, [k]: !value[k] })}
          className={cx("rounded border px-2 py-1 text-[11px] transition-colors", value[k] ? `border-line bg-panel-2 ${color}` : "border-line/60 text-faint hover:text-muted")}>
          {label}
        </button>
      ))}
    </div>
  );
}

const IMPORTANCE: Record<ChartEvent["importance"], string> = {
  high: "border-down/50 text-down", medium: "border-warn/50 text-warn", low: "border-line text-muted",
};

/** Clickable list of the news markers on the chart (also reachable by keyboard). */
export function NewsMarkerList({ events, onSelect }: { events: ChartEvent[]; onSelect: (e: ChartEvent) => void }) {
  if (!events.length) return <p className="text-xs text-faint">No calendar events in the visible range.</p>;
  return (
    <ul className="flex flex-wrap gap-2" data-testid="news-markers">
      {events.map((e) => (
        <li key={e.id}>
          <button type="button" onClick={() => onSelect(e)}
            className={cx("rounded border px-2 py-1 text-left text-[11px] transition-colors hover:bg-panel-2", IMPORTANCE[e.importance], !e.affects_symbol && "opacity-60")}>
            <span className="font-mono">{formatEt(e.ts)}</span> · {e.title}
          </button>
        </li>
      ))}
    </ul>
  );
}

export function NewsPanel({ event, onClose }: { event: ChartEvent; onClose: () => void }) {
  const row = (k: string, v: React.ReactNode) => (
    <div className="grid grid-cols-[8rem_1fr] gap-2 py-1"><dt className="text-muted">{k}</dt><dd>{v}</dd></div>
  );
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onMouseDown={onClose}>
      <aside role="dialog" aria-modal="true" aria-label="News event" data-testid="news-panel"
        className="h-full w-full max-w-md overflow-y-auto border-l border-line bg-panel p-5 shadow-2xl motion-safe:animate-[slidein_.18s_ease-out]"
        onMouseDown={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-start justify-between gap-3">
          <h2 className="text-base font-semibold">{event.title}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-faint hover:text-text">✕</button>
        </div>
        <dl className="text-sm">
          {row("Headline", event.title)}
          {row("Source", event.kind === "news" ? `${event.source} (official feed)` : event.source === "manual" ? "Manual entry" : event.source_ref ?? event.source)}
          {row(event.kind === "news" ? "Published" : "Scheduled time", formatEt(event.ts, true))}
          {row(event.kind === "news" ? "Detected by APEX" : "Recorded in APEX", event.detected_at ? formatEt(event.detected_at, true) : "—")}
          {row("Impact level", <span className={cx("rounded border px-1.5 py-px text-xs uppercase", IMPORTANCE[event.importance])}>{event.impact ?? event.importance}</span>)}
          {row("Category", event.category)}
          {row("Affected markets", event.affected.join(", ") || "none mapped")}
          {row("Directional bias", event.bias)}
        </dl>
        <p className="mt-4 text-xs text-muted">{event.explanation}</p>
        {event.source_ref?.startsWith("http") ? (
          <a href={event.source_ref} target="_blank" rel="noreferrer noopener" className="mt-3 inline-block text-xs text-accent hover:underline">Original source →</a>
        ) : <p className="mt-3 text-xs text-faint">{event.kind === "news" ? "The source provided no link." : "No article link: this is a scheduled calendar event, not a news article."}</p>}
        <p className="mt-4 text-[11px] text-faint">PROJECT APEX does not attribute price moves to events. Official feeds (Federal Reserve, EIA) are connected; real-time breaking-news wires need a licensed provider and are not connected.</p>
      </aside>
    </div>
  );
}
