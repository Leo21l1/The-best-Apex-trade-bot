import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
  UTCTimestamp,
} from "lightweight-charts";

type Target = Parameters<IPrimitivePaneRenderer["draw"]>[0];

/** A shaded price rectangle from t1 to t2 (null = extends to the right edge). Times in bar seconds. */
export interface ZoneShape { id: string; t1: number; t2: number | null; top: number; bottom: number; fill: string; stroke: string; dashed?: boolean; label?: string }
/** A horizontal segment at ``price`` from t1 to t2 with an optional label at its right end. */
export interface SegmentShape { id: string; t1: number; t2: number; price: number; color: string; dashed?: boolean; label?: string; width?: number }

class Renderer implements IPrimitivePaneRenderer {
  constructor(private src: ZonesPrimitive) {}

  draw(target: Target) {
    const { chart, series } = this.src;
    if (!chart || !series) return;
    const ts = chart.timeScale();
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr, bitmapSize }) => {
      const x = (t: number | null) => {
        if (t === null) return bitmapSize.width;
        const c = ts.timeToCoordinate(t as UTCTimestamp);
        return c === null ? null : c * hr;
      };
      const y = (p: number) => {
        const c = series.priceToCoordinate(p);
        return c === null ? null : c * vr;
      };
      ctx.font = `${10 * vr}px ui-monospace, monospace`;
      for (const z of this.src.zones) {
        const x1 = x(z.t1) ?? 0;
        const x2 = x(z.t2) ?? bitmapSize.width;
        const y1 = y(z.top);
        const y2 = y(z.bottom);
        if (y1 === null || y2 === null || x2 < 0) continue;
        const h = Math.max(1, y2 - y1);
        ctx.fillStyle = z.fill;
        ctx.fillRect(x1, y1, Math.max(1, x2 - x1), h);
        ctx.strokeStyle = z.stroke;
        ctx.lineWidth = hr;
        ctx.setLineDash(z.dashed ? [4 * hr, 3 * hr] : []);
        ctx.strokeRect(x1, y1, Math.max(1, x2 - x1), h);
        if (z.label && x2 - x1 > 30 * hr) {
          ctx.fillStyle = z.stroke;
          ctx.fillText(z.label, x1 + 3 * hr, y1 + 10 * vr);
        }
      }
      for (const s of this.src.segments) {
        const x1 = x(s.t1);
        const x2 = x(s.t2);
        const yy = y(s.price);
        if (x1 === null || x2 === null || yy === null) continue;
        ctx.strokeStyle = s.color;
        ctx.lineWidth = (s.width ?? 1.5) * hr;
        ctx.setLineDash(s.dashed ? [5 * hr, 4 * hr] : []);
        ctx.beginPath();
        ctx.moveTo(x1, yy);
        ctx.lineTo(x2, yy);
        ctx.stroke();
        if (s.label) {
          ctx.fillStyle = s.color;
          ctx.fillText(s.label, x2 + 3 * hr, yy - 3 * vr);
        }
      }
      ctx.setLineDash([]);
      if (this.src.highlight !== null) {
        const yy = y(this.src.highlight);
        if (yy !== null) {
          ctx.fillStyle = "rgba(56,189,248,0.18)";
          ctx.fillRect(0, yy - 3 * vr, bitmapSize.width, 6 * vr);
        }
      }
    });
  }
}

class View implements IPrimitivePaneView {
  private r: Renderer;
  constructor(src: ZonesPrimitive) { this.r = new Renderer(src); }
  zOrder() { return "bottom" as const; }
  renderer() { return this.r; }
}

/** Series primitive that draws SMC zones and level segments beneath the candles. */
export class ZonesPrimitive implements ISeriesPrimitive<Time> {
  zones: ZoneShape[] = [];
  segments: SegmentShape[] = [];
  highlight: number | null = null;
  chart: IChartApi | null = null;
  series: ISeriesApi<"Candlestick"> | null = null;
  private views: View[];
  private request: (() => void) | null = null;

  constructor() { this.views = [new View(this)]; }

  attached(p: SeriesAttachedParameter<Time>) {
    this.chart = p.chart as IChartApi;
    this.series = p.series as ISeriesApi<"Candlestick">;
    this.request = p.requestUpdate;
  }

  detached() { this.chart = null; this.series = null; this.request = null; }

  paneViews() { return this.views; }

  set(zones: ZoneShape[], segments: SegmentShape[], highlight: number | null) {
    this.zones = zones;
    this.segments = segments;
    this.highlight = highlight;
    this.request?.();
  }

  /** Topmost zone containing the point (most recently added zones are drawn last). */
  hit(t: number, price: number): ZoneShape | null {
    for (let i = this.zones.length - 1; i >= 0; i--) {
      const z = this.zones[i]!;
      if (t >= z.t1 && (z.t2 === null || t <= z.t2) && price <= z.top && price >= z.bottom) return z;
    }
    return null;
  }
}
