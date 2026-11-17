"use client";

import type { OpportunitiesResponse, Opportunity } from "@apex/shared-types";
import { Fragment, useMemo, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, EmptyState, Panel, QualityBadge } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { formatEt, formatUsd } from "@/lib/format";

const STATUS_CLS: Record<Opportunity["status"], string> = {
  active: "text-accent border-accent/30",
  invalidated: "text-down border-down/30",
  target_reached: "text-up border-up/30",
  expired: "text-faint border-line",
};
const px = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 2 });

export function Opportunities() {
  const { data, error } = usePolling<OpportunitiesResponse>("/api/opportunities?hours=24", 10_000);
  const [symbol, setSymbol] = useState("");
  const [strategy, setStrategy] = useState("");
  const [activeOnly, setActiveOnly] = useState(true);
  const [open, setOpen] = useState<number | null>(null);

  const symbols = useMemo(() => [...new Set((data?.candidates ?? []).map((c) => c.symbol))].sort(), [data]);
  const strategies = useMemo(() => [...new Set((data?.candidates ?? []).map((c) => c.strategy_id))].sort(), [data]);
  const rows = useMemo(
    () =>
      (data?.candidates ?? []).filter(
        (c) => (!symbol || c.symbol === symbol) && (!strategy || c.strategy_id === strategy) && (!activeOnly || c.status === "active"),
      ),
    [data, symbol, strategy, activeOnly],
  );
  const select = "rounded-md border border-line bg-panel-2 px-2 py-1.5 text-sm text-text";

  return (
    <>
      <PageHeader path="/opportunities">
        {data && <QualityBadge quality={data.quality} />}
      </PageHeader>
      {error && <div className="mb-4"><Callout tone="danger" title="Request failed">{error}</Callout></div>}

      <Panel title="Ranked opportunities">
        {data && data.ranked.length > 0 ? (
          <ul className="space-y-2 text-sm">
            {data.ranked.map((o) => (
              <li key={o.id} className="font-mono">{o.symbol} {o.direction} · {o.strategy_id} · score {o.score}</li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No ranked opportunities">
            <span data-testid="ranked-empty">
              {data?.ranking_rule ?? "Loading…"} No strategy has passed research validation yet, so nothing is ranked.
            </span>
          </EmptyState>
        )}
      </Panel>

      <div className="mt-4">
        <Panel title="Research candidates (unranked, not trade recommendations)">
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <select aria-label="Symbol filter" className={select} value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              <option value="">All symbols</option>
              {symbols.map((s) => <option key={s}>{s}</option>)}
            </select>
            <select aria-label="Strategy filter" className={select} value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              <option value="">All strategies</option>
              {strategies.map((s) => <option key={s}>{s}</option>)}
            </select>
            <label className="flex items-center gap-2 text-sm text-muted">
              <input type="checkbox" checked={activeOnly} onChange={(e) => setActiveOnly(e.target.checked)} /> Active only
            </label>
            <span className="ml-auto text-xs text-faint">{data?.score_note}</span>
          </div>
          {!data ? (
            <p className="text-sm text-faint">Loading…</p>
          ) : rows.length === 0 ? (
            <EmptyState title="No candidates">
              No strategy conditions are currently met for the selected filters. That is a normal result.
            </EmptyState>
          ) : (
            <div className="-mx-4 overflow-x-auto">
              <table className="w-full min-w-[900px] text-sm" data-testid="candidates-table">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-faint">
                    {["Time (ET)", "Symbol", "Strategy", "Side", "Entry zone", "Stop", "Targets", "R:R", "Risk/contract", "Score", "Status"].map((h) => (
                      <th key={h} className="px-3 py-2 font-medium first:pl-4">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((o) => (
                    <Fragment key={o.id}>
                      <tr
                        className="cursor-pointer border-b border-line/60 hover:bg-panel-2/40"
                        onClick={() => setOpen(open === o.id ? null : o.id)}
                      >
                        <td className="py-2 pl-4 pr-3 font-mono text-xs text-muted">{formatEt(o.created_at)}</td>
                        <td className="px-3 py-2 font-mono">{o.symbol}</td>
                        <td className="px-3 py-2 font-mono text-xs">{o.strategy_id}</td>
                        <td className={cx("px-3 py-2", o.direction === "long" ? "text-up" : "text-down")}>{o.direction}</td>
                        <td className="px-3 py-2 font-mono">{px(o.entry_low)}–{px(o.entry_high)}</td>
                        <td className="px-3 py-2 font-mono">{px(o.stop)}</td>
                        <td className="px-3 py-2 font-mono">{o.targets.map(px).join(" / ")}</td>
                        <td className="px-3 py-2 font-mono">{o.reward_risk.toFixed(2)}</td>
                        <td className="px-3 py-2 font-mono">{formatUsd(o.loss_at_stop_after_costs)}</td>
                        <td className="px-3 py-2 font-mono">{o.score.toFixed(0)}</td>
                        <td className="px-3 py-2">
                          <span className={cx("rounded border px-1.5 py-px text-xs", STATUS_CLS[o.status])}>{o.status.replace("_", " ")}</span>
                        </td>
                      </tr>
                      {open === o.id && (
                        <tr className="border-b border-line/60 bg-panel-2/30">
                          <td colSpan={11} className="px-4 py-3"><Explain o={o} /></td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}

function Explain({ o }: { o: Opportunity }) {
  return (
    <div className="grid gap-4 text-xs lg:grid-cols-3">
      <div>
        <p className="mb-1 font-medium text-muted uppercase">Why it triggered</p>
        <ul className="space-y-0.5">
          {o.conditions.map((c) => (
            <li key={c.name} className="text-muted"><span className="text-up">✓</span> {c.name}: {c.detail}</li>
          ))}
        </ul>
      </div>
      <div>
        <p className="mb-1 font-medium text-muted uppercase">Setup score {o.score.toFixed(0)} / 100</p>
        <ul className="space-y-0.5">
          {o.score_breakdown.map((p) => (
            <li key={p.name} className="flex justify-between gap-3 text-muted">
              <span>{p.name} <span className="text-faint">({p.detail})</span></span>
              <span className="font-mono">{p.points}/{p.max}</span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-faint">
          Risk {formatUsd(o.risk_per_contract)} + assumed cost {formatUsd(o.estimated_cost_per_contract)} per contract.
          Data: {o.provider} ({o.quality}) at {formatEt(o.data_ts, true)}.
        </p>
      </div>
      <div>
        <p className="mb-1 font-medium text-warn uppercase">Why it is not ranked</p>
        <ul className="list-disc space-y-0.5 pl-4 text-muted">
          {o.rank_blockers.map((b) => <li key={b}>{b}</li>)}
        </ul>
        <a
          className="mt-2 inline-block text-accent hover:underline"
          href={`/copilot?q=${encodeURIComponent(`Explain research candidate #${o.id}: ${o.symbol} ${o.direction} ${o.strategy_id}. Why did it trigger, what invalidates it, and what is the risk for 1 contract?`)}`}
        >
          Ask the copilot to explain this setup →
        </a>
        <p className="mt-2 mb-1 font-medium text-muted uppercase">Uncertainty</p>
        <ul className="list-disc space-y-0.5 pl-4 text-muted">
          {o.uncertainty.map((u) => <li key={u}>{u}</li>)}
          <li className={o.news?.status === "inside_window" ? "text-warn" : undefined}>News: {o.news?.summary ?? o.news_exposure}</li>
          <li>Risk check (1 contract): {o.risk ? `${o.risk.verdict.replaceAll("_", " ")}; max size ${o.risk.max_size ?? "unknown"}` : o.risk_eligibility}</li>
        </ul>
      </div>
    </div>
  );
}
