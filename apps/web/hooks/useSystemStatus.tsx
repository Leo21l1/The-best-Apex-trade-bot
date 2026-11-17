"use client";

import type { SystemStatus } from "@apex/shared-types";
import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { type Connection, useRealtime } from "./useRealtime";

export type { Connection };

interface StatusCtx {
  status: SystemStatus | null;
  connection: Connection;
  /** Epoch ms when the last status was received (from WS or polling). */
  receivedAt: number | null;
  error: string | null;
}

const Ctx = createContext<StatusCtx | null>(null);
const POLL_MS = 10_000;

export function SystemStatusProvider({ children }: { children: React.ReactNode }) {
  const { connection, subscribe } = useRealtime();
  const [state, setState] = useState<Omit<StatusCtx, "connection">>({
    status: null,
    receivedAt: null,
    error: null,
  });

  useEffect(
    () =>
      subscribe("system.status", (data) =>
        setState({ status: data as SystemStatus, receivedAt: Date.now(), error: null }),
      ),
    [subscribe],
  );

  // Poll while the realtime channel is not open (initial load and outages).
  useEffect(() => {
    if (connection === "open") return;
    let disposed = false;
    const poll = async () => {
      try {
        const s = await api<SystemStatus>("/api/system/status");
        if (!disposed) setState({ status: s, receivedAt: Date.now(), error: null });
      } catch (e) {
        if (!disposed) setState((p) => ({ ...p, error: (e as Error).message }));
      }
    };
    void poll();
    const t = setInterval(poll, POLL_MS);
    return () => {
      disposed = true;
      clearInterval(t);
    };
  }, [connection]);

  return <Ctx.Provider value={{ ...state, connection }}>{children}</Ctx.Provider>;
}

export function useSystemStatus(): StatusCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSystemStatus must be used inside SystemStatusProvider");
  return v;
}
