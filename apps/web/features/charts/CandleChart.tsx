"use client";

import type { Bar } from "@apex/shared-types";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import { MARKET_TZ } from "@/lib/format";

const COLORS = {
  bg: "#15181d",
  text: "#8b939e",
  grid: "#1f242b",
  border: "#262b33",
  up: "#3fb68b",
  down: "#e5575b",
};

const etTime = new Intl.DateTimeFormat("en-US", {
  timeZone: MARKET_TZ, hour: "2-digit", minute: "2-digit", hour12: false,
});
const etDate = new Intl.DateTimeFormat("en-US", { timeZone: MARKET_TZ, month: "short", day: "numeric" });
const etFull = new Intl.DateTimeFormat("en-US", {
  timeZone: MARKET_TZ, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  hour12: false,
});
const utcDate = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" });

const toCandle = (b: Bar) => ({ time: b.t as UTCTimestamp, open: b.o, high: b.h, low: b.l, close: b.c });
const toVolume = (b: Bar) => ({
  time: b.t as UTCTimestamp,
  value: b.v,
  color: b.c >= b.o ? "rgba(63,182,139,0.35)" : "rgba(229,87,91,0.35)",
});

/**
 * Candlestick + volume chart. Axis times are shown in New York time for intraday bars;
 * daily/weekly bars are stamped with their trading date.
 */
export function CandleChart({
  bars,
  tick,
  daily,
  seriesKey,
  onHover,
}: {
  bars: Bar[];
  tick: number;
  /** True for 1d/1w, whose timestamps are trading dates (midnight UTC). */
  daily: boolean;
  /** Changes when symbol/timeframe change, forcing a full redraw and refit. */
  seriesKey: string;
  onHover?: (bar: Bar | null) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volume = useRef<ISeriesApi<"Histogram"> | null>(null);
  const drawn = useRef<{ key: string; first: number; length: number } | null>(null);
  const barsRef = useRef(bars);
  const hoverRef = useRef(onHover);
  const dailyRef = useRef(daily);

  useEffect(() => {
    barsRef.current = bars;
    hoverRef.current = onHover;
    dailyRef.current = daily;
  });

  useEffect(() => {
    if (!container.current) return;
    const fmt = (t: Time, full: boolean) => {
      const d = new Date((t as number) * 1000);
      if (dailyRef.current) return utcDate.format(d);
      return full ? `${etFull.format(d)} ET` : etTime.format(d);
    };
    const c = createChart(container.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: COLORS.bg },
        textColor: COLORS.text,
        fontSize: 11,
        attributionLogo: true,
      },
      grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: COLORS.border },
      timeScale: {
        borderColor: COLORS.border,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
        tickMarkFormatter: (t: Time) => {
          const d = new Date((t as number) * 1000);
          if (dailyRef.current) return etDate.format(new Date((t as number) * 1000 + 12 * 3600_000));
          const label = etTime.format(d);
          return label === "18:00" || label === "00:00" ? etDate.format(d) : label;
        },
      },
      localization: { timeFormatter: (t: Time) => fmt(t, true) },
    });
    chart.current = c;
    candles.current = c.addSeries(CandlestickSeries, {
      upColor: COLORS.up, downColor: COLORS.down, borderVisible: false,
      wickUpColor: COLORS.up, wickDownColor: COLORS.down,
    });
    volume.current = c.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false,
      priceLineVisible: false,
    });
    volume.current.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    candles.current.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.22 } });
    c.subscribeCrosshairMove((param) => {
      const t = param.time as number | undefined;
      hoverRef.current?.(t === undefined ? null : (barsRef.current.find((b) => b.t === t) ?? null));
    });
    return () => {
      c.remove();
      chart.current = null;
      candles.current = null;
      volume.current = null;
      drawn.current = null;
    };
  }, []);

  useEffect(() => {
    const decimals = Math.max(0, (tick.toString().split(".")[1] ?? "").length);
    candles.current?.applyOptions({
      priceFormat: { type: "price", precision: decimals, minMove: tick },
    });
  }, [tick]);

  useEffect(() => {
    const cs = candles.current;
    const vs = volume.current;
    if (!cs || !vs) return;
    const prev = drawn.current;
    const first = bars[0]?.t ?? 0;
    const incremental =
      prev !== null &&
      prev.key === seriesKey &&
      prev.first === first &&
      bars.length > 0 &&
      (bars.length === prev.length || bars.length === prev.length + 1);
    if (incremental) {
      // Live tick: update the last bar (and the one before it when a new bar was appended).
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

  return <div ref={container} className="h-[440px] w-full sm:h-[520px]" data-testid="candle-chart" />;
}
