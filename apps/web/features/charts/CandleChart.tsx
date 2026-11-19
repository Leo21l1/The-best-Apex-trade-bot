"use client";

import type { Bar, ChartEvent, Overlays } from "@apex/shared-types";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  LineSeries,
  LineStyle,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import { MARKET_TZ } from "@/lib/format";
import type { MarkerOut, SegmentOut, ZoneOut } from "@/lib/smc";
import { ZonesPrimitive } from "./zones";

const COLORS = {
  bg: "#0b1220", text: "#93a1b8", grid: "#152034", border: "#1e2b42",
  up: "#10b981", down: "#ef4444", ema20: "#22d3ee", ema50: "#a78bfa", vwap: "#f59e0b",
  high: "#ef4444", medium: "#f59e0b", low: "#93a1b8",
};

const etTime = new Intl.DateTimeFormat("en-US", { timeZone: MARKET_TZ, hour: "2-digit", minute: "2-digit", hour12: false });
const etDate = new Intl.DateTimeFormat("en-US", { timeZone: MARKET_TZ, month: "short", day: "numeric" });
const etFull = new Intl.DateTimeFormat("en-US", { timeZone: MARKET_TZ, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
const utcDate = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" });

const toCandle = (b: Bar) => ({ time: b.t as UTCTimestamp, open: b.o, high: b.h, low: b.l, close: b.c });
const toVolume = (b: Bar) => ({ time: b.t as UTCTimestamp, value: b.v, color: b.c >= b.o ? "rgba(16,185,129,0.32)" : "rgba(239,68,68,0.32)" });

export interface PriceLineSpec { price: number; title: string; color: string; dashed?: boolean }
export interface ExtraMarker { t: number; text: string; color: string; position: "aboveBar" | "belowBar"; shape: "arrowUp" | "arrowDown" | "circle" | "square" }
export interface OverlayToggles { ema: boolean; vwap: boolean; levels: boolean; signals: boolean; news: boolean }

const LEVEL_NAMES: Record<string, string> = {
  prior_day_high: "PDH", prior_day_low: "PDL", prior_day_close: "PDC", overnight_high: "ONH", overnight_low: "ONL",
  opening_range_high: "ORH", opening_range_low: "ORL",
};

/**
 * Candlestick + volume chart with optional analysis overlays (EMA, VWAP, levels, strategy signals,
 * news markers), extra price lines (entry/stop/target) and markers. Times shown in New York time.
 */
export function CandleChart({
  bars, tick, daily, seriesKey, onHover, overlays, toggles, lines, markers, onEventClick, height,
  smc, highlightPrice, onPatternClick, showVolume = true,
}: {
  bars: Bar[];
  tick: number;
  daily: boolean;
  seriesKey: string;
  onHover?: (bar: Bar | null) => void;
  overlays?: Overlays | null;
  toggles?: OverlayToggles;
  lines?: PriceLineSpec[];
  markers?: ExtraMarker[];
  onEventClick?: (e: ChartEvent) => void;
  height?: string;
  /** Smart-money shapes (zones, level segments, markers) from ``patternShapes``. */
  smc?: { zones: ZoneOut[]; segments: SegmentOut[]; markers: MarkerOut[] } | null;
  highlightPrice?: number | null;
  onPatternClick?: (id: string) => void;
  /** Volume histogram visibility (default on). */
  showVolume?: boolean;
}) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volume = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lineSeries = useRef<{ ema20?: ISeriesApi<"Line">; ema50?: ISeriesApi<"Line">; vwap?: ISeriesApi<"Line"> }>({});
  const markerApi = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const priceLines = useRef<IPriceLine[]>([]);
  const drawn = useRef<{ key: string; first: number; length: number } | null>(null);
  const barsRef = useRef(bars);
  const hoverRef = useRef(onHover);
  const dailyRef = useRef(daily);
  const eventsRef = useRef<ChartEvent[]>([]);
  const clickRef = useRef(onEventClick);
  const patternClickRef = useRef(onPatternClick);
  const zones = useRef<ZonesPrimitive | null>(null);

  useEffect(() => {
    barsRef.current = bars;
    hoverRef.current = onHover;
    dailyRef.current = daily;
    clickRef.current = onEventClick;
    patternClickRef.current = onPatternClick;
  });

  useEffect(() => {
    if (!container.current) return;
    const c = createChart(container.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: COLORS.bg }, textColor: COLORS.text, fontSize: 11, attributionLogo: true },
      grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: COLORS.border },
      timeScale: {
        borderColor: COLORS.border, timeVisible: true, secondsVisible: false, rightOffset: 6,
        tickMarkFormatter: (t: Time) => {
          const d = new Date((t as number) * 1000);
          if (dailyRef.current) return etDate.format(new Date((t as number) * 1000 + 12 * 3600_000));
          const label = etTime.format(d);
          return label === "18:00" || label === "00:00" ? etDate.format(d) : label;
        },
      },
      localization: {
        timeFormatter: (t: Time) => {
          const d = new Date((t as number) * 1000);
          return dailyRef.current ? utcDate.format(d) : `${etFull.format(d)} ET`;
        },
      },
    });
    chart.current = c;
    candles.current = c.addSeries(CandlestickSeries, {
      upColor: COLORS.up, downColor: COLORS.down, borderVisible: false, wickUpColor: COLORS.up, wickDownColor: COLORS.down,
    });
    volume.current = c.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false, priceLineVisible: false });
    volume.current.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    candles.current.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.22 } });
    const mk = (color: string) => c.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    lineSeries.current = { ema20: mk(COLORS.ema20), ema50: mk(COLORS.ema50), vwap: mk(COLORS.vwap) };
    markerApi.current = createSeriesMarkers(candles.current, []);
    zones.current = new ZonesPrimitive();
    candles.current.attachPrimitive(zones.current);
    c.subscribeCrosshairMove((param) => {
      const t = param.time as number | undefined;
      hoverRef.current?.(t === undefined ? null : (barsRef.current.find((b) => b.t === t) ?? null));
    });
    c.subscribeClick((param) => {
      const t = param.time as number | undefined;
      if (t === undefined) return;
      const ev = eventsRef.current.find((e) => e.t === t);
      if (ev) { clickRef.current?.(ev); return; }
      const hovered = typeof param.hoveredObjectId === "string" ? param.hoveredObjectId : "";
      if (hovered.startsWith("smc-")) { patternClickRef.current?.(hovered.slice(4)); return; }
      const z = zones.current;
      const cs = candles.current;
      if (!z || !cs || !param.point) return;
      const price = cs.coordinateToPrice(param.point.y);
      const seg = z.segments.find((s) => t >= s.t1 && t <= s.t2 && Math.abs((cs.priceToCoordinate(s.price) ?? -99) - param.point!.y) <= 4);
      if (seg) { patternClickRef.current?.(seg.id); return; }
      const hit = price === null ? null : z.hit(t, price);
      if (hit) patternClickRef.current?.(hit.id);
    });
    return () => {
      c.remove();
      chart.current = null;
      candles.current = null;
      volume.current = null;
      markerApi.current = null;
      zones.current = null;
      priceLines.current = [];
      drawn.current = null;
    };
  }, []);

  useEffect(() => {
    const decimals = Math.max(0, (tick.toString().split(".")[1] ?? "").length);
    candles.current?.applyOptions({ priceFormat: { type: "price", precision: decimals, minMove: tick } });
  }, [tick]);

  useEffect(() => {
    const cs = candles.current;
    const vs = volume.current;
    if (!cs || !vs) return;
    const prev = drawn.current;
    const first = bars[0]?.t ?? 0;
    const incremental = prev !== null && prev.key === seriesKey && prev.first === first && bars.length > 0 &&
      (bars.length === prev.length || bars.length === prev.length + 1);
    if (incremental) {
      for (const b of bars.slice(bars.length === prev.length ? -1 : -2)) {
        cs.update(toCandle(b));
        vs.update(toVolume(b));
      }
    } else {
      cs.setData(bars.map(toCandle));
      vs.setData(bars.map(toVolume));
      if (prev?.key !== seriesKey) chart.current?.timeScale().scrollToRealTime();
    }
    drawn.current = { key: seriesKey, first, length: bars.length };
  }, [bars, seriesKey]);

  // Overlays: indicator lines, level lines, markers.
  useEffect(() => {
    const cs = candles.current;
    if (!cs) return;
    const show = toggles ?? { ema: true, vwap: true, levels: true, signals: true, news: true };
    const visible = new Set(bars.map((b) => b.t));
    const ls = lineSeries.current;
    const toLine = (pts: { t: number; v: number }[]) => pts.filter((p) => visible.has(p.t)).map((p) => ({ time: p.t as UTCTimestamp, value: p.v }));
    ls.ema20?.setData(show.ema && overlays ? toLine(overlays.ema20) : []);
    ls.ema50?.setData(show.ema && overlays ? toLine(overlays.ema50) : []);
    ls.vwap?.setData(show.vwap && overlays ? toLine(overlays.vwap) : []);

    for (const pl of priceLines.current) cs.removePriceLine(pl);
    priceLines.current = [];
    if (show.levels && overlays) {
      for (const [k, v] of Object.entries(overlays.levels)) {
        priceLines.current.push(cs.createPriceLine({ price: v, color: "rgba(139,147,158,0.55)", lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: LEVEL_NAMES[k] ?? k }));
      }
    }
    for (const l of lines ?? []) {
      priceLines.current.push(cs.createPriceLine({ price: l.price, color: l.color, lineWidth: 2, lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid, axisLabelVisible: true, title: l.title }));
    }

    const ms: SeriesMarker<Time>[] = [];
    if (show.signals && overlays) {
      for (const s of overlays.signals) {
        if (!visible.has(s.t)) continue;
        ms.push({ time: s.t as UTCTimestamp, position: s.direction === "long" ? "belowBar" : "aboveBar", shape: s.direction === "long" ? "arrowUp" : "arrowDown", color: s.direction === "long" ? COLORS.up : COLORS.down, text: `${s.label} ${s.strategy_id}` });
      }
    }
    const evs = show.news && overlays ? overlays.events.filter((e) => visible.has(e.t)) : [];
    eventsRef.current = evs;
    for (const e of evs) {
      ms.push({ time: e.t as UTCTimestamp, position: "aboveBar", shape: "square", color: COLORS[e.importance], text: e.importance === "high" ? "NEWS" : "news", id: `ev-${e.id}` });
    }
    for (const m of markers ?? []) {
      if (visible.has(m.t)) ms.push({ time: m.t as UTCTimestamp, position: m.position, shape: m.shape, color: m.color, text: m.text });
    }
    for (const m of smc?.markers ?? []) {
      if (visible.has(m.t)) ms.push({ time: m.t as UTCTimestamp, position: m.position, shape: m.shape, color: m.color, text: m.text, id: `smc-${m.id}` });
    }
    zones.current?.set(smc?.zones ?? [], smc?.segments ?? [], highlightPrice ?? null);
    ms.sort((a, b) => (a.time as number) - (b.time as number));
    markerApi.current?.setMarkers(ms);
  }, [overlays, toggles, lines, markers, bars, smc, highlightPrice]);

  useEffect(() => {
    volume.current?.applyOptions({ visible: showVolume });
  }, [showVolume]);

  return <div ref={container} className={height ?? "h-[440px] w-full sm:h-[520px]"} data-testid="candle-chart" />;
}
