"use client";

import { ColorType, createChart, LineSeries, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";

/** Cumulative net P&L after each trade (x = exit time). */
export function EquityChart({ points }: { points: { t: number; v: number }[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || points.length === 0) return;
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#15181d" }, textColor: "#8b939e", fontSize: 11, attributionLogo: true },
      grid: { vertLines: { color: "#1f242b" }, horzLines: { color: "#1f242b" } },
      rightPriceScale: { borderColor: "#262b33" },
      timeScale: { borderColor: "#262b33", timeVisible: true },
    });
    const last = points[points.length - 1]!.v;
    const series = chart.addSeries(LineSeries, { color: last >= 0 ? "#3fb68b" : "#e5575b", lineWidth: 2 });
    // Strictly increasing times are required; nudge identical exit times by one second.
    let prev = 0;
    series.setData(points.map((p) => {
      const t = Math.max(p.t, prev + 1);
      prev = t;
      return { time: t as UTCTimestamp, value: p.v };
    }));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points]);
  return <div ref={ref} className="h-56 w-full" data-testid="equity-chart" />;
}
