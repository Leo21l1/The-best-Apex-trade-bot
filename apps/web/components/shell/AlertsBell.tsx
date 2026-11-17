"use client";

import type { Alert, AlertsResponse } from "@apex/shared-types";
import { useCallback, useEffect, useState } from "react";
import { useRealtime } from "@/hooks/useRealtime";
import { api } from "@/lib/api";
import { formatEt } from "@/lib/format";
import { cx } from "@/components/ui/primitives";

const SEVERITY: Record<Alert["severity"], string> = {
  info: "bg-accent", warning: "bg-warn", critical: "bg-down",
};

export function AlertsBell() {
  const { subscribe } = useRealtime();
  const [data, setData] = useState<AlertsResponse | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(() => {
    api<AlertsResponse>("/api/alerts?limit=30").then(setData).catch(() => undefined);
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    const off = subscribe("alerts", load);
    return () => {
      clearInterval(t);
      off();
    };
  }, [load, subscribe]);

  const readAll = async () => {
    await api("/api/alerts/read-all", { method: "POST" }).catch(() => undefined);
    load();
  };
  const unread = data?.unread ?? 0;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={`Alerts, ${unread} unread`}
        aria-expanded={open}
        className="relative rounded p-1.5 text-muted hover:bg-panel-2 hover:text-text"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0" />
        </svg>
        {unread > 0 && (
          <span data-testid="alerts-unread" className="absolute -top-0.5 -right-0.5 min-w-4 rounded-full bg-warn px-1 text-center text-[10px] font-semibold text-bg">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Alerts"
          className="absolute right-0 z-40 mt-2 w-[min(24rem,calc(100vw-2rem))] rounded-lg border border-line bg-panel shadow-2xl"
        >
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <p className="text-xs font-medium tracking-wide text-muted uppercase">Alerts</p>
            <button type="button" onClick={() => void readAll()} className="text-xs text-accent hover:underline" disabled={unread === 0}>
              Mark all read
            </button>
          </div>
          <ul className="max-h-96 overflow-y-auto">
            {(data?.alerts.length ?? 0) === 0 && <li className="px-3 py-6 text-center text-sm text-faint">No alerts</li>}
            {data?.alerts.map((a) => (
              <li key={a.id} className={cx("border-b border-line/60 px-3 py-2 last:border-0", !a.read && "bg-panel-2/50")}>
                <p className="flex items-start gap-2 text-sm">
                  <span className={cx("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", SEVERITY[a.severity])} aria-hidden />
                  <span>{a.title}</span>
                </p>
                <p className="mt-0.5 pl-3.5 text-xs text-muted">{a.body}</p>
                <p className="mt-0.5 pl-3.5 font-mono text-[11px] text-faint">{formatEt(a.created_at, true)} · {a.kind.replaceAll("_", " ")}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
