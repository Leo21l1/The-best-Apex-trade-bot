"use client";

import type { ChartEvent, Instrument, SmcPattern, Timeframe } from "@apex/shared-types";
import { useEffect, useMemo, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, Panel, QualityBadge } from "@/components/ui/primitives";
import { CollapsiblePanel, ResizableSplit } from "@/components/ui/viz";
import { usePolling } from "@/hooks/usePolling";
import { formatPrice } from "@/lib/candles";
import { patternShapes } from "@/lib/smc";
import { CandleChart, type OverlayToggles } from "../charts/CandleChart";
import { NewsMarkerList, NewsPanel, OverlayToggleBar, useOverlays } from "../charts/overlayParts";
import { useCandles } from "../charts/useCandles";
import { EntryConfirmationPanel } from "../session/EntryConfirmation";
import { ReadinessPanel } from "../session/ReadinessPanel";
import { ConfluencePanel, LiquidityMapPanel, PatternInspector, PatternList, SmcToggleBar, useConfluence, useSmc, useSmcSettings } from "./parts";

const SMC_TFS: Timeframe[] = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"];

/** Smart Money Analysis: SMC overlays, pattern inspector, liquidity map and MTF confluence. */
export function SmcPage() {
  const [symbol, setSymbol] = useState("ES");
  const [tf, setTf] = useState<Timeframe>("5m");
  const [settings, setSettings] = useSmcSettings();
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("symbol");
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time read of the URL after mount
    if (q) setSymbol(q.toUpperCase());
  }, []);
  const [selected, setSelected] = useState<SmcPattern | null>(null);
  const [level, setLevel] = useState<number | null>(null);
  const [event, setEvent] = useState<ChartEvent | null>(null);
  const [ov, setOv] = useState<OverlayToggles>({ ema: true, vwap: true, levels: false, signals: false, news: true });
  const { data: instruments } = usePolling<Instrument[]>("/api/market/instruments", 3_600_000);
  const { bars, provenance } = useCandles(symbol, tf);
  const { data: analysis, error } = useSmc(symbol, tf);
  const { data: conf } = useConfluence(symbol);
  const overlays = useOverlays(symbol, tf);
  const tick = instruments?.find((i) => i.symbol === symbol)?.tick_size ?? 0.25;
  const times = useMemo(() => bars.map((b) => b.t), [bars]);
  const shapes = useMemo(
    () => (analysis ? patternShapes(analysis.patterns, times, settings.toggles, { minQuality: settings.minQuality, maxPerFamily: settings.maxZones, selected: selected?.id ?? null }) : null),
    [analysis, times, settings, selected],
  );
  const pick = (id: string) => setSelected(analysis?.patterns.find((p) => p.id === id) ?? null);
  const quality = provenance?.quality ?? "unavailable";

  return (
    <>
      <PageHeader path="/smc" />
      {quality === "simulated" && (
        <div className="mb-4"><Callout tone="warn" title="SIMULATED DATA">Patterns below are detected on synthetic prices. They demonstrate the detectors, not real market structure.</Callout></div>
      )}
      {error && <div className="mb-4"><Callout tone="danger" title="Analysis unavailable">{error}</Callout></div>}
      <ResizableSplit id="smc" left={<>
          <Panel>
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <select aria-label="SMC instrument" value={symbol} onChange={(e) => { setSymbol(e.target.value); setSelected(null); setLevel(null); }}
                className="rounded-md border border-line bg-panel-2 px-2 py-1.5 font-mono text-sm">
                {(instruments ?? [{ symbol: "ES" } as Instrument]).map((i) => <option key={i.symbol} value={i.symbol}>{i.symbol}</option>)}
              </select>
              <div role="group" aria-label="SMC timeframe" className="flex rounded-md border border-line">
                {SMC_TFS.map((t) => (
                  <button key={t} type="button" aria-pressed={tf === t} onClick={() => { setTf(t); setSelected(null); }}
                    className={cx("px-2 py-1 font-mono text-xs", tf === t ? "bg-accent/15 text-accent" : "text-muted hover:text-text")}>{t}</button>
                ))}
              </div>
              {bars.length > 0 && <span className="font-mono text-lg" data-testid="smc-last">{formatPrice(bars[bars.length - 1]!.c, tick)}</span>}
              <QualityBadge quality={quality} />
              {analysis && <span className="text-xs text-faint" data-testid="smc-count">{analysis.patterns.length} patterns · {analysis.bars} closed bars</span>}
            </div>
            <div className="mb-2 flex flex-wrap gap-3">
              <SmcToggleBar value={settings} onChange={setSettings} />
              <OverlayToggleBar value={ov} onChange={setOv} />
            </div>
            <CandleChart bars={bars} tick={tick} daily={tf === "1d"} seriesKey={`smc-${symbol}-${tf}`} overlays={overlays} toggles={ov}
              smc={shapes} highlightPrice={level} onPatternClick={pick} onEventClick={setEvent} />
            <div className="mt-2"><NewsMarkerList events={overlays?.events ?? []} onSelect={setEvent} /></div>
            <p className="mt-2 text-[11px] text-faint">
              Yellow: liquidity sweeps · green/red boxes: order blocks · dashed boxes: fair value gaps · blue line: BOS · purple: CHoCH/MSS ·
              click a zone, line or marker to inspect it. Only closed bars are analysed.
            </p>
          </Panel>
          {selected && <PatternInspector p={selected} analysis={analysis} onClose={() => setSelected(null)} />}
          <CollapsiblePanel id="smc-confluence" title="Multi-timeframe confluence"><ConfluencePanel c={conf} /></CollapsiblePanel>
          <EntryConfirmationPanel symbol={symbol} refreshKey={bars.length ? bars[bars.length - 1]!.t : null} />
        </>} right={<>
          <ReadinessPanel symbol={symbol} refreshKey={bars.length ? bars[bars.length - 1]!.t : null} />
          <CollapsiblePanel id="smc-liquidity" title="Liquidity map"><LiquidityMapPanel map={conf?.liquidity} selected={level} onSelect={(l) => setLevel(level === l.price ? null : l.price)} /></CollapsiblePanel>
          <CollapsiblePanel id="smc-patterns" title="Smart money analysis"><PatternList analysis={analysis} selected={selected?.id ?? null} onSelect={setSelected} /></CollapsiblePanel>
        </>} />
      {event && <NewsPanel event={event} onClose={() => setEvent(null)} />}
    </>
  );
}
