"use client";

import { useState } from "react";
import { cx, Panel } from "@/components/ui/primitives";
import { api } from "@/lib/api";

interface TestResult { target: string; state: "ok" | "degraded" | "down" | "not_configured"; detail: string; latency_ms: number }

const TARGETS: { target: string; label: string; note: string }[] = [
  { target: "news:fed", label: "Federal Reserve press releases (RSS)", note: "Official public feed; free." },
  { target: "news:eia", label: "EIA Today in Energy (RSS)", note: "Official public feed; free." },
  { target: "databento", label: "Databento CME futures data", note: "Licensed data; needs APEX_DATABENTO_API_KEY. Test lists datasets only (no data billed)." },
  { target: "anthropic", label: "Claude API (AI copilot)", note: "Needs APEX_ANTHROPIC_API_KEY. Test lists models only (no tokens used)." },
];

const TONE: Record<TestResult["state"], string> = {
  ok: "text-up", degraded: "text-warn", down: "text-down", not_configured: "text-faint",
};

/** Admin-only, free connection tests. A result reflects that moment only; nothing is cached as connected. */
export function IntegrationTests() {
  const [res, setRes] = useState<Record<string, TestResult | "running" | string>>({});
  const run = async (target: string) => {
    setRes((r) => ({ ...r, [target]: "running" }));
    try {
      const out = await api<TestResult>("/api/integrations/test", { method: "POST", body: { target } });
      setRes((r) => ({ ...r, [target]: out }));
    } catch (e) {
      setRes((r) => ({ ...r, [target]: (e as Error).message }));
    }
  };
  return (
    <Panel title="Connection tests">
      <ul className="space-y-3" data-testid="integration-tests">
        {TARGETS.map((t) => {
          const r = res[t.target];
          return (
            <li key={t.target} className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <p className="text-sm">{t.label}</p>
                <p className="text-xs text-faint">{t.note}</p>
                {r && r !== "running" && (
                  typeof r === "string"
                    ? <p className="text-xs text-down">{r}</p>
                    : <p className={cx("text-xs", TONE[r.state])} data-testid={`test-${t.target}`}>{r.state.replace("_", " ").toUpperCase()} · {r.detail} · {r.latency_ms} ms</p>
                )}
              </div>
              <button type="button" onClick={() => void run(t.target)} disabled={r === "running"}
                className="rounded border border-line px-2 py-1 text-xs hover:border-accent disabled:opacity-50">
                {r === "running" ? "Testing…" : "Test"}
              </button>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-[11px] text-faint">Tradovate live order entry is not an integration: it is permanently disabled. Keys are set as server environment variables, never in the browser.</p>
    </Panel>
  );
}
