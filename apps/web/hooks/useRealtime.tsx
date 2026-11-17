"use client";

import type { WsFrame } from "@apex/shared-types";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { backoffDelay } from "@/lib/backoff";

export type Connection = "connecting" | "open" | "closed";
type Handler = (data: unknown, frame: WsFrame) => void;

interface RealtimeCtx {
  connection: Connection;
  /** Listen to a topic. Market topics (`candles:<SYMBOL>`) are subscribed on the server. */
  subscribe: (topic: string, handler: Handler) => () => void;
}

const Ctx = createContext<RealtimeCtx | null>(null);

function wsUrl(): string {
  const configured = process.env.NEXT_PUBLIC_APEX_WS_URL;
  if (configured) return configured;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.hostname}:8000/api/ws`;
}

const needsServerSubscription = (topic: string) => topic.startsWith("candles:");

/** One shared WebSocket per dashboard session, with reconnect and automatic resubscription. */
export function RealtimeProvider({ children }: { children: React.ReactNode }) {
  const [connection, setConnection] = useState<Connection>("connecting");
  const listeners = useRef(new Map<string, Set<Handler>>());
  const socket = useRef<WebSocket | null>(null);

  const send = useCallback((msg: object) => {
    const ws = socket.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  }, []);

  useEffect(() => {
    let disposed = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const scheduleReconnect = () => {
      if (disposed) return;
      clearTimeout(timer);
      timer = setTimeout(connect, backoffDelay(attempt++));
    };

    const connect = () => {
      if (disposed) return;
      setConnection("connecting");
      let ws: WebSocket;
      try {
        ws = new WebSocket(wsUrl());
      } catch {
        scheduleReconnect();
        return;
      }
      socket.current = ws;
      ws.onopen = () => {
        attempt = 0;
        setConnection("open");
        const topics = [...listeners.current.keys()].filter(needsServerSubscription);
        if (topics.length) ws.send(JSON.stringify({ type: "subscribe", topics }));
      };
      ws.onmessage = (ev) => {
        let frame: WsFrame;
        try {
          frame = JSON.parse(String(ev.data)) as WsFrame;
        } catch {
          return;
        }
        listeners.current.get(frame.topic)?.forEach((fn) => fn(frame.data, frame));
      };
      ws.onclose = () => {
        if (socket.current === ws) socket.current = null;
        setConnection("closed");
        scheduleReconnect();
      };
    };

    connect();
    return () => {
      disposed = true;
      clearTimeout(timer);
      const ws = socket.current;
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
    };
  }, []);

  const subscribe = useCallback(
    (topic: string, handler: Handler) => {
      let set = listeners.current.get(topic);
      if (!set) {
        set = new Set();
        listeners.current.set(topic, set);
        if (needsServerSubscription(topic)) send({ type: "subscribe", topics: [topic] });
      }
      set.add(handler);
      return () => {
        const cur = listeners.current.get(topic);
        if (!cur) return;
        cur.delete(handler);
        if (cur.size === 0) {
          listeners.current.delete(topic);
          if (needsServerSubscription(topic)) send({ type: "unsubscribe", topics: [topic] });
        }
      };
    },
    [send],
  );

  const value = useMemo(() => ({ connection, subscribe }), [connection, subscribe]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useRealtime(): RealtimeCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useRealtime must be used inside RealtimeProvider");
  return v;
}
