"use client";

import type { SystemStatus, WsFrame } from "@apex/shared-types";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { backoffDelay } from "@/lib/backoff";

export type Connection = "connecting" | "open" | "closed";

interface StatusCtx {
  status: SystemStatus | null;
  connection: Connection;
  /** Epoch ms when the last status was received (from WS or polling). */
  receivedAt: number | null;
  error: string | null;
}

const Ctx = createContext<StatusCtx | null>(null);
const POLL_MS = 10_000;

function wsUrl(): string {
  const configured = process.env.NEXT_PUBLIC_APEX_WS_URL;
  if (configured) return configured;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.hostname}:8000/api/ws`;
}

export function SystemStatusProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<StatusCtx>({
    status: null,
    connection: "connecting",
    receivedAt: null,
    error: null,
  });
  const attempt = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let pollTimer: ReturnType<typeof setInterval> | undefined;
    let disposed = false;

    const poll = async () => {
      try {
        const s = await api<SystemStatus>("/api/system/status");
        if (!disposed) setState((p) => ({ ...p, status: s, receivedAt: Date.now(), error: null }));
      } catch (e) {
        if (!disposed) setState((p) => ({ ...p, error: (e as Error).message }));
      }
    };

    const startPolling = () => {
      if (pollTimer) return;
      void poll();
      pollTimer = setInterval(poll, POLL_MS);
    };
    const stopPolling = () => {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = undefined;
    };

    const connect = () => {
      if (disposed) return;
      setState((p) => ({ ...p, connection: "connecting" }));
      try {
        ws = new WebSocket(wsUrl());
      } catch {
        scheduleReconnect();
        return;
      }
      ws.onopen = () => {
        attempt.current = 0;
        stopPolling();
        setState((p) => ({ ...p, connection: "open", error: null }));
      };
      ws.onmessage = (ev) => {
        try {
          const frame = JSON.parse(String(ev.data)) as WsFrame;
          if (frame.topic === "system.status") {
            setState((p) => ({
              ...p,
              status: frame.data as SystemStatus,
              receivedAt: Date.now(),
              error: null,
            }));
          }
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        setState((p) => ({ ...p, connection: "closed" }));
        startPolling();
        scheduleReconnect();
      };
    };

    const scheduleReconnect = () => {
      if (disposed) return;
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, backoffDelay(attempt.current++));
    };

    startPolling();
    connect();
    return () => {
      disposed = true;
      clearTimeout(reconnectTimer);
      stopPolling();
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
    };
  }, []);

  return <Ctx.Provider value={state}>{children}</Ctx.Provider>;
}

export function useSystemStatus(): StatusCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSystemStatus must be used inside SystemStatusProvider");
  return v;
}
