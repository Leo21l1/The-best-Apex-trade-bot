"use client";

import { useState } from "react";
import { cx, Panel } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { useSession } from "@/hooks/useSession";
import { api } from "@/lib/api";

interface ForwardRow { setup: string; trades: number; win_rate: number; net: number; expectancy: number; expectancy_ci: [number, number] | null }
interface AutopilotStatus {
  mode: string;
  kill_switch: { at: string | null; by: string } | null;
  strategies: string[];
  account: { status: string; balance: number; equity: number; day_pnl: number; daily_loss_remaining: number } | null;
  positions: Record<string, { qty: number; avg_price: number }>;
  forward: ForwardRow[];
  forward_total: { trades: number; net: number };
  prospective_evidence: { sufficient: boolean; detail: string };
  last_cycle: { at: string; halted?: Record<string, string>; broker_gate?: string[]; note?: string } | null;
  label: string;
}

const MODE_TEXT: Record<string, string> = {
  APEX_MANUAL: "Manual: scanning and alerts only, never any order",
  AUTONOMOUS_PAPER: "Autonomous paper: simulated orders on a separate paper account",
  AUTHORIZED_BROKER: "Authorized broker requested: refused unless every gate passes (no broker adapter is installed)",
};

/** Autopilot status, kill switch and forward (prospective) simulated results. Never a real order. */
export function AutopilotPanel() {
  const { data, error } = usePolling<AutopilotStatus>("/api/autopilot", 15_000);
  const { user } = useSession();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const toggle = async (kill: boolean) => {
    setBusy(true); setMsg(null);
    try {
      await api(`/api/autopilot/${kill ? "kill" : "resume"}`, { method: "POST" });
      setMsg(kill ? "Kill switch ON: no new entries; simulated orders are cancelled and positions flattened on the next cycle." : "Kill switch OFF.");
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel title="Autopilot (SIMULATED)">
      {error && !data && <p className="text-xs text-down">{error}</p>}
      {data && (
        <div className="space-y-2 text-xs" data-testid="autopilot">
          <p className="text-muted">{data.label}</p>
          <p><span className="font-mono text-text" data-testid="autopilot-mode">{data.mode}</span> - {MODE_TEXT[data.mode] ?? data.mode}</p>
          {data.kill_switch && <p className="text-down" data-testid="autopilot-killed">KILL SWITCH ON since {data.kill_switch.at} ({data.kill_switch.by})</p>}
          {data.last_cycle?.halted && Object.keys(data.last_cycle.halted).length > 0 && (
            <p className="text-warn">No new entries: {Object.entries(data.last_cycle.halted).map(([k, v]) => `${k === "*" ? "all" : k}: ${v}`).join("; ")}</p>
          )}
          {data.last_cycle?.broker_gate && <p className="text-down">Real-money gate refused: {data.last_cycle.broker_gate.join("; ")}</p>}
          <p className="text-faint">Pre-registered setups: {data.strategies.join(", ")}</p>
          {data.account && (
            <p className="font-mono">Simulated account {data.account.status}: balance ${data.account.balance}, equity ${data.account.equity}, day ${data.account.day_pnl}, daily loss left ${data.account.daily_loss_remaining}</p>
          )}
          <table className="w-full text-left text-[11px]" data-testid="autopilot-forward">
            <thead className="text-faint"><tr><th>Forward setup</th><th>Trades</th><th>Win rate</th><th>$/trade (95% CI)</th><th>Net</th></tr></thead>
            <tbody>
              {data.forward.map((r) => (
                <tr key={r.setup} className="border-t border-line/40 font-mono">
                  <td>{r.setup}</td><td>{r.trades}</td><td>{(r.win_rate * 100).toFixed(1)}%</td>
                  <td className={cx(r.expectancy > 0 ? "text-up" : "text-down")}>{r.expectancy} {r.expectancy_ci ? `(${r.expectancy_ci[0]} – ${r.expectancy_ci[1]})` : ""}</td><td>{r.net}</td>
                </tr>
              ))}
              {!data.forward.length && <tr><td colSpan={5} className="text-faint">No closed autonomous trades yet.</td></tr>}
            </tbody>
          </table>
          <p className={cx(data.prospective_evidence.sufficient ? "text-up" : "text-faint")} data-testid="autopilot-evidence">
            Prospective evidence: {data.prospective_evidence.sufficient ? "sufficient" : "not yet sufficient"} ({data.prospective_evidence.detail})
          </p>
          {user?.role === "admin" && (
            <div className="flex gap-2">
              <button type="button" disabled={busy || !!data.kill_switch} onClick={() => void toggle(true)} data-testid="autopilot-kill"
                className="rounded border border-down/60 px-2 py-1 font-semibold text-down hover:bg-down/10 disabled:opacity-40">EMERGENCY STOP</button>
              <button type="button" disabled={busy || !data.kill_switch} onClick={() => void toggle(false)}
                className="rounded border border-line px-2 py-1 text-muted hover:bg-panel-2 disabled:opacity-40">Resume</button>
            </div>
          )}
          {msg && <p className="text-muted">{msg}</p>}
        </div>
      )}
    </Panel>
  );
}
