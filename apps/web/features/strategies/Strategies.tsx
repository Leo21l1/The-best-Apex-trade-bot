"use client";

import type { StrategiesResponse } from "@apex/shared-types";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, Panel, StatePill } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";

const FIELDS = [
  ["Market regime", "regime"], ["Entry", "entry"], ["Invalidation", "invalidation"], ["Stop", "stop"],
  ["Targets", "targets"], ["Time exit", "time_exit"], ["Required data", "required_data"],
  ["Testing method", "testing"], ["Estimated costs", "costs"],
] as const;

export function Strategies() {
  const { data, error } = usePolling<StrategiesResponse>("/api/strategies", 300_000);
  return (
    <>
      <PageHeader path="/strategies" />
      <div className="mb-4">
        <Callout tone="warn" title="No strategy is validated">
          These are rule definitions, not proven edges. None is assumed profitable. A strategy can
          contribute ranked opportunities only after its backtest evidence meets the validation
          standards below (backtesting arrives in Phase 6).
        </Callout>
      </div>
      {error && <Callout tone="danger" title="Request failed">{error}</Callout>}
      {data && (
        <>
          <Panel title="Validation standards">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
              {Object.entries(data.validation_standards).map(([k, v]) => (
                <div key={k} className="flex justify-between gap-3">
                  <dt className="text-muted">{k.replaceAll("_", " ")}</dt>
                  <dd className="font-mono">{String(v)}</dd>
                </div>
              ))}
            </dl>
          </Panel>
          <div className="mt-4 grid gap-4 xl:grid-cols-2">
            {data.strategies.map((s) => (
              <Panel
                key={s.id}
                title={<span data-testid={`strategy-${s.id}`}>{s.code} · {s.name}</span>}
                actions={<StatePill state={s.validation.validated ? "ok" : "degraded"} label={s.validation.validated ? "Validated" : "Unvalidated"} />}
              >
                <p className="mb-2 text-xs text-faint">
                  Timeframe {s.timeframe} · Instruments {s.eligible_instruments.join(", ")}
                </p>
                <dl className="space-y-1.5 text-sm">
                  {FIELDS.map(([label, key]) => (
                    <div key={key} className="grid grid-cols-[7.5rem_1fr] gap-3">
                      <dt className="text-xs text-muted">{label}</dt>
                      <dd className="text-text/90">{s[key]}</dd>
                    </div>
                  ))}
                </dl>
                <p className="mt-2 text-xs text-faint">{s.validation.reason}</p>
              </Panel>
            ))}
          </div>
        </>
      )}
    </>
  );
}
