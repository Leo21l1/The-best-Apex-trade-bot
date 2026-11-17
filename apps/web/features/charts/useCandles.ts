"use client";

import type { Bar, CandlesResponse, LiveCandle, Provenance, Timeframe } from "@apex/shared-types";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRealtime } from "@/hooks/useRealtime";
import { api } from "@/lib/api";
import { applyLiveUpdate, mergeBars } from "@/lib/candles";

const INITIAL = 600;
const REFRESH_MS = 20_000;

interface State {
  key: string;
  bars: Bar[];
  provenance: Provenance | null;
  error: string | null;
  loading: boolean;
  /** Epoch ms of the last live update received over the WebSocket. */
  liveAt: number | null;
}

const url = (symbol: string, tf: Timeframe, limit: number, before?: number) =>
  `/api/market/candles?symbol=${symbol}&timeframe=${tf}&limit=${limit}` +
  (before ? `&before=${encodeURIComponent(new Date(before * 1000).toISOString())}` : "");

/** Historical bars + live updates for one symbol/timeframe. */
export function useCandles(symbol: string, tf: Timeframe) {
  const key = `${symbol}:${tf}`;
  const { subscribe } = useRealtime();
  const [state, setState] = useState<State>({
    key, bars: [], provenance: null, error: null, loading: true, liveAt: null,
  });
  const keyRef = useRef(key);
  useEffect(() => {
    keyRef.current = key;
  }, [key]);

  // Refresh the newest bars from the server (authoritative aggregation + provenance).
  const refreshTail = useCallback(
    async (limit: number) => {
      try {
        const res = await api<CandlesResponse>(url(symbol, tf, limit));
        if (keyRef.current !== key) return;
        setState((p) => ({
          key,
          bars: p.key === key ? mergeBars(p.bars, res.bars) : res.bars,
          provenance: res.provenance,
          error: null,
          loading: false,
          liveAt: p.key === key ? p.liveAt : null,
        }));
      } catch (e) {
        if (keyRef.current !== key) return;
        setState((p) => ({ ...p, key, error: (e as Error).message, loading: false }));
      }
    },
    [symbol, tf, key],
  );

  useEffect(() => {
    void refreshTail(INITIAL);
    const t = setInterval(() => void refreshTail(3), REFRESH_MS);
    return () => clearInterval(t);
  }, [refreshTail]);

  useEffect(
    () =>
      subscribe(`candles:${symbol}`, (data) => {
        const u = data as LiveCandle;
        setState((p) =>
          p.key !== key || p.bars.length === 0
            ? p
            : { ...p, bars: applyLiveUpdate(p.bars, u, tf), liveAt: Date.now() },
        );
        if (u.closed && tf !== "1m") void refreshTail(3);
      }),
    [subscribe, symbol, tf, key, refreshTail],
  );

  const loadOlder = useCallback(async () => {
    const first = state.bars[0];
    if (!first) return 0;
    const res = await api<CandlesResponse>(url(symbol, tf, INITIAL, first.t));
    if (keyRef.current !== key) return 0;
    setState((p) => ({ ...p, bars: mergeBars(res.bars, p.bars) }));
    return res.bars.length;
  }, [state.bars, symbol, tf, key]);

  const current = state.key === key;
  return {
    bars: current ? state.bars : [],
    provenance: current ? state.provenance : null,
    error: current ? state.error : null,
    loading: !current || state.loading,
    liveAt: current ? state.liveAt : null,
    loadOlder,
  };
}
