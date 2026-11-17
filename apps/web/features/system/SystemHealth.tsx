"use client";

import { useEffect, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, Panel, Stat, StatePill } from "@/components/ui/primitives";
import { useSystemStatus } from "@/hooks/useSystemStatus";
import { formatAge, formatEt, humanizeComponent } from "@/lib/format";

export function SystemHealth() {
  const { status, connection, receivedAt, error } = useSystemStatus();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const age = receivedAt ? Math.floor((now - receivedAt) / 1000) : Number.POSITIVE_INFINITY;
  const stale = age > 20;

  return (
    <>
      <PageHeader path="/system" />
      {error && (
        <div className="mb-4">
          <Callout tone="danger" title="Status request failed">{error}</Callout>
        </div>
      )}
      {stale && receivedAt && (
        <div className="mb-4">
          <Callout tone="warn" title="Status is stale">
            Last update {formatAge(age)}. Values below may not reflect current state.
          </Callout>
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Panel>
          <Stat label="Infrastructure" value={status ? <StatePill state={status.overall} /> : "—"} hint="Data feeds and integrations listed below" />
        </Panel>
        <Panel>
          <Stat
            label="Realtime channel"
            value={<span data-testid="ws-state">{connection}</span>}
            hint={connection === "open" ? "WebSocket" : "Falling back to polling"}
          />
        </Panel>
        <Panel>
          <Stat label="Last update" value={receivedAt ? formatAge(age) : "—"} />
        </Panel>
        <Panel>
          <Stat
            label="Execution mode"
            value={status?.execution_mode ?? "—"}
            hint={status ? `Live order routing: ${status.live_order_routing ? "ON" : "disabled"}` : undefined}
          />
        </Panel>
      </div>

      <div className="mt-4">
        <Panel title="Components">
          {!status ? (
            <p className="text-sm text-faint">Loading…</p>
          ) : (
            <div className="-mx-4 overflow-x-auto">
              <table className="w-full min-w-[640px] text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-faint">
                    <th className="px-4 py-2 font-medium">Component</th>
                    <th className="px-4 py-2 font-medium">State</th>
                    <th className="px-4 py-2 font-medium">Detail</th>
                    <th className="px-4 py-2 text-right font-medium">Latency</th>
                    <th className="px-4 py-2 text-right font-medium">Checked</th>
                  </tr>
                </thead>
                <tbody>
                  {status.components.map((c) => (
                    <tr key={c.name} className="border-b border-line/60 last:border-0" data-testid={`component-${c.name}`}>
                      <td className="px-4 py-2.5">{humanizeComponent(c.name)}</td>
                      <td className="px-4 py-2.5"><StatePill state={c.state} /></td>
                      <td className="px-4 py-2.5 text-muted">{c.detail}</td>
                      <td className="px-4 py-2.5 text-right font-mono text-muted">
                        {c.latency_ms != null ? `${c.latency_ms.toFixed(1)} ms` : "—"}
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono text-xs text-faint">
                        {formatEt(c.checked_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
      {status && (
        <p className="mt-3 text-xs text-faint">
          {status.app} v{status.version} · {status.environment} · server time{" "}
          {formatEt(status.server_time, true)}
        </p>
      )}
    </>
  );
}
