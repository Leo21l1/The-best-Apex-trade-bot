"use client";

import type { Bar, ChartEvent, GapReport, Instrument, Timeframe } from "@apex/shared-types";
import { useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, EmptyState, Panel, QualityBadge } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { formatPrice, TIMEFRAMES } from "@/lib/candles";
import { formatEt } from "@/lib/format";
import { CandleChart, type OverlayToggles } from "./CandleChart";
import { NewsMarkerList, NewsPanel, OverlayToggleBar, SignalBadge, useOverlays } from "./overlayParts";
import { ReadinessPanel } from "../session/ReadinessPanel";
import { useCandles } from "./useCandles";

export function Charts() {
  const [symbol, setSymbol] = useState("ES");
  const [tf, setTf] = useState<Timeframe>("5m");
  const [hover, setHover] = useState<Bar | null>(null);
  const [olderNote, setOlderNote] = useState<string | null>(null);
  const [toggles, setToggles] = useState<OverlayToggles>({ ema: true, vwap: true, levels: true, signals: true, news: true });
  const [event, setEvent] = useState<ChartEvent | null>(null);
  const overlays = useOverlays(symbol, tf);
  const { data: instruments } = usePolling<Instrument[]>("/api/market/instruments", 3_600_000);
  const { bars, provenance, error, loading, liveAt, loadOlder } = useCandles(symbol, tf);
  const { data: gaps } = usePolling<GapReport>(`/api/market/gaps?symbol=${symbol}&hours=24`, 60_000);

  const inst = instruments?.find((i) => i.symbol === symbol);
  const tick = inst?.tick_size ?? 0.01;
  const last = bars[bars.length - 1];
  const shown = hover ?? last;
  const quality = provenance?.quality ?? "unavailable";
  const unavailable = quality === "unavailable";
  const daily = tf === "1d" || tf === "1w";

  return (
    <>
      <PageHeader path="/charts" />

      {quality === "simulated" && (
        <div className="mb-4">
          <Callout tone="warn" title="SIMULATED DATA">
            These are synthetic prices generated for development. They are not real market
            prices and must not be used for trading decisions.
          </Callout>
        </div>
      )}
      {provenance?.stale && !unavailable && (
        <div className="mb-4">
          <Callout tone="warn" title="Data is stale">
            {provenance.note ?? "No recent bar has arrived while the market is open."} Last bar:{" "}
            {provenance.last_bar ? formatEt(provenance.last_bar, true) : "none"}.
          </Callout>
        </div>
      )}
      {error && (
        <div className="mb-4">
          <Callout tone="danger" title="Could not load market data">{error}</Callout>
        </div>
      )}

      <Panel>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
          <label className="text-xs text-muted">
            <span className="sr-only">Instrument</span>
            <select
              aria-label="Instrument"
              value={symbol}
              onChange={(e) => {
                setSymbol(e.target.value);
                setHover(null);
                setOlderNote(null);
              }}
              className="rounded-md border border-line bg-panel-2 px-2 py-1.5 font-mono text-sm text-text"
            >
              {(instruments ?? [{ symbol: "ES", name: "E-mini S&P 500" }]).map((i) => (
                <option key={i.symbol} value={i.symbol}>
                  {i.symbol} — {i.name}
                </option>
              ))}
            </select>
          </label>

          <div role="group" aria-label="Timeframe" className="flex overflow-hidden rounded-md border border-line">
            {TIMEFRAMES.map((t) => (
              <button
                key={t}
                type="button"
                aria-pressed={t === tf}
                onClick={() => {
                  setTf(t);
                  setHover(null);
                  setOlderNote(null);
                }}
                className={cx(
                  "px-2.5 py-1.5 font-mono text-xs",
                  t === tf ? "bg-accent/15 text-accent" : "text-muted hover:bg-panel-2",
                )}
              >
                {t}
              </button>
            ))}
          </div>

          <div className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
            <SignalBadge label={overlays?.label} />
            <QualityBadge quality={quality} />
            <span data-testid="chart-provider">
              Provider: <span className="font-mono text-text">{provenance?.provider ?? "none"}</span>
            </span>
            {last && (
              <span data-testid="chart-last-time">
                Last bar: <span className="font-mono text-text">{formatEt(new Date(last.t * 1000), true)}</span>
              </span>
            )}
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-baseline gap-x-5 gap-y-1 font-mono text-xs text-muted" aria-live="off">
          <span className="text-2xl text-text" data-testid="chart-last-price">
            {last ? formatPrice(last.c, tick) : "—"}
          </span>
          {shown && (
            <>
              <span>O <span className="text-text">{formatPrice(shown.o, tick)}</span></span>
              <span>H <span className="text-text">{formatPrice(shown.h, tick)}</span></span>
              <span>L <span className="text-text">{formatPrice(shown.l, tick)}</span></span>
              <span>C <span className="text-text">{formatPrice(shown.c, tick)}</span></span>
              <span>Vol <span className="text-text">{shown.v.toLocaleString("en-US")}</span></span>
            </>
          )}
          {liveAt && <span className="text-faint">stream update {formatEt(new Date(liveAt))}</span>}
        </div>

        <div className="mt-3">
          {unavailable ? (
            <EmptyState title="No market data">
              {provenance?.note ?? "Loading…"} Set <code className="font-mono">APEX_MARKET_DATA_PROVIDER</code>{" "}
              on the server to enable a provider. No prices are shown without one.
            </EmptyState>
          ) : bars.length === 0 ? (
            <EmptyState title={loading ? "Loading bars…" : "No bars stored yet"}>
              {!loading && (provenance?.note ?? "The ingestion worker has not stored bars for this symbol.")}
            </EmptyState>
          ) : (
            <CandleChart bars={bars} tick={tick} daily={daily} seriesKey={`${symbol}:${tf}`} onHover={setHover}
              overlays={overlays?.symbol === symbol && overlays.timeframe === tf ? overlays : null} toggles={toggles} onEventClick={setEvent} />
          )}
        </div>

        {!unavailable && bars.length > 0 && (
          <div className="mt-3 space-y-2">
            <OverlayToggleBar value={toggles} onChange={setToggles} />
            {toggles.news && <NewsMarkerList events={(overlays?.events ?? []).filter((e) => e.t >= (bars[0]?.t ?? 0))} onSelect={setEvent} />}
          </div>
        )}
        {event && <NewsPanel event={event} onClose={() => setEvent(null)} />}

        {!unavailable && bars.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-faint">
            <span>
              {bars.length.toLocaleString("en-US")} bars · times in New York (ET)
              {daily ? " · daily bars follow the 18:00 ET trading day" : ""}
            </span>
            <span className="flex items-center gap-3">
              {olderNote && <span>{olderNote}</span>}
              <button
                type="button"
                className="rounded border border-line px-2 py-1 text-muted hover:bg-panel-2"
                onClick={() =>
                  void loadOlder().then((n) => setOlderNote(n ? `Loaded ${n} older bars` : "No older bars stored"))
                }
              >
                Load older bars
              </button>
            </span>
          </div>
        )}
      </Panel>

      <div className="mt-4">
        <ReadinessPanel symbol={symbol} refreshKey={bars.length ? bars[bars.length - 1]!.t : null} />
      </div>

      {!unavailable && gaps && (
        <div className="mt-4">
          <Panel title={`Data quality — ${symbol}, last 24h of 1-minute bars`}>
            <dl className="grid grid-cols-3 gap-4 text-sm">
              <div><dt className="text-xs text-muted">Expected</dt><dd className="font-mono">{gaps.expected_bars.toLocaleString("en-US")}</dd></div>
              <div><dt className="text-xs text-muted">Stored</dt><dd className="font-mono">{gaps.present_bars.toLocaleString("en-US")}</dd></div>
              <div>
                <dt className="text-xs text-muted">Missing</dt>
                <dd className={cx("font-mono", gaps.missing_bars > 0 && "text-warn")} data-testid="gap-missing">
                  {gaps.missing_bars.toLocaleString("en-US")}
                </dd>
              </div>
            </dl>
            {gaps.gaps.length > 0 && (
              <ul className="mt-3 space-y-1 font-mono text-xs text-muted">
                {gaps.gaps.slice(0, 8).map((g) => (
                  <li key={g.start}>
                    {formatEt(g.start, true)} → {formatEt(g.end)} · {g.missing_bars} bar{g.missing_bars === 1 ? "" : "s"}
                  </li>
                ))}
                {gaps.gaps.length > 8 && <li>… and {gaps.gaps.length - 8} more</li>}
              </ul>
            )}
            <p className="mt-3 text-xs text-faint">{gaps.note}</p>
          </Panel>
        </div>
      )}
    </>
  );
}
