"use client";

import type { CopilotAnswer, CopilotHistoryItem, CopilotStatus, CopilotToolCall } from "@apex/shared-types";
import { useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Callout, cx, Panel } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { api, ApiError } from "@/lib/api";
import { formatEt } from "@/lib/format";
import { AnswerBody, ToolTrace } from "./Answer";

interface Turn {
  id: string;
  question: string;
  answer?: string;
  error?: string;
  toolCalls: CopilotToolCall[];
  meta?: string;
  pending?: boolean;
}

export const SUGGESTIONS = [
  "What is the strongest setup right now?",
  "Scan ES and NQ.",
  "What is the current market regime?",
  "How much drawdown room do I have?",
  "What is the risk of a 1-contract ES long at the last price with a 4-point stop?",
  "Which setups have the best historical expectancy?",
  "Are there economic events I should know about today?",
  "Summarize my trading session.",
];

function fromHistory(h: CopilotHistoryItem): Turn {
  return {
    id: `h${h.id}`, question: h.question, answer: h.answer ?? undefined, error: h.error ?? undefined,
    toolCalls: h.tool_calls ?? [], meta: `${formatEt(h.created_at, true)}${h.model ? ` · ${h.model}` : ""}`,
  };
}

export function Copilot() {
  const { data: status } = usePolling<CopilotStatus>("/api/copilot/status", 300_000);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Prepend saved history; never overwrite turns the user started while it was loading.
    api<CopilotHistoryItem[]>("/api/copilot/history?limit=20")
      .then((h) => setTurns((p) => [...h.map(fromHistory).filter((x) => !p.some((t) => t.id === x.id)), ...p]))
      .catch(() => undefined);
    const q = new URLSearchParams(window.location.search).get("q");
    // One-time read of the URL after mount (a prerendered page cannot know it at render time).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (q) setInput(q.slice(0, 2000));
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const send = async (question: string) => {
    const q = question.trim();
    if (!q || busy) return;
    const id = `t${Date.now()}`;
    const history = turns
      .filter((t) => t.answer)
      .flatMap((t) => [{ role: "user", content: t.question }, { role: "assistant", content: t.answer! }]);
    setTurns((p) => [...p, { id, question: q, toolCalls: [], pending: true }]);
    setInput("");
    setBusy(true);
    try {
      const r = await api<CopilotAnswer>("/api/copilot/ask", { method: "POST", body: { question: q, history } });
      setTurns((p) => p.map((t) => t.id === id ? {
        ...t, pending: false, answer: r.answer, toolCalls: r.tool_calls,
        meta: [r.model, r.fallback_used ? "fallback model used" : null, r.note].filter(Boolean).join(" · "),
      } : t));
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Request failed";
      setTurns((p) => p.map((t) => (t.id === id ? { ...t, pending: false, error: msg } : t)));
    } finally {
      setBusy(false);
    }
  };

  const disabled = status?.configured === false;

  return (
    <>
      <PageHeader path="/copilot">
        {status && (
          <span className="text-xs text-faint">
            {status.configured ? `${status.model} · effort ${status.effort}` : "Not configured"}
          </span>
        )}
      </PageHeader>

      <div className="mb-4 space-y-3">
        {disabled && (
          <Callout tone="warn" title="AI copilot is not configured">
            Set <code className="font-mono">APEX_ANTHROPIC_API_KEY</code> on the API server and restart it. The key is
            never sent to the browser. Everything else in PROJECT APEX works without it.
          </Callout>
        )}
        <Callout tone="info" title="Answers come from your application data">
          The copilot reads data only through read-only tools and shows every call it made. Risk numbers come from the
          deterministic Risk Guardian, not the model. It cannot place, modify or cancel orders. {status?.data_sent}
        </Callout>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_18rem]">
        <Panel className="flex min-h-[28rem] flex-col">
          <div className="flex-1 space-y-5" aria-live="polite" data-testid="copilot-thread">
            {turns.length === 0 && (
              <p className="py-10 text-center text-sm text-faint">Ask about setups, levels, regime, risk, rules or events.</p>
            )}
            {turns.map((t) => (
              <div key={t.id} className="space-y-2">
                <div className="flex justify-end">
                  <p className="max-w-[85%] rounded-lg bg-accent/15 px-3 py-2 text-sm text-text">{t.question}</p>
                </div>
                <div className="rounded-lg border border-line bg-panel-2/40 px-4 py-3" data-testid="copilot-answer">
                  {t.pending ? (
                    <p className="animate-pulse text-sm text-faint">Reading application data…</p>
                  ) : t.error ? (
                    <p role="alert" className="text-sm text-down">{t.error}</p>
                  ) : (
                    <>
                      <AnswerBody text={t.answer ?? ""} />
                      <ToolTrace calls={t.toolCalls} />
                    </>
                  )}
                  {t.meta && <p className="mt-2 text-[11px] text-faint">{t.meta}</p>}
                </div>
              </div>
            ))}
            <div ref={bottom} />
          </div>
          <form
            className="mt-4 flex gap-2 border-t border-line pt-4"
            onSubmit={(e) => {
              e.preventDefault();
              void send(input);
            }}
          >
            <textarea
              aria-label="Ask the copilot"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send(input);
                }
              }}
              maxLength={2000}
              rows={2}
              disabled={disabled}
              placeholder={disabled ? "Copilot not configured" : "Ask a question (Enter to send, Shift+Enter for a new line)"}
              className="flex-1 resize-none rounded-md border border-line bg-panel-2 px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-50"
            />
            <button type="submit" disabled={busy || disabled || !input.trim()} className="self-end rounded-md bg-accent px-4 py-2 text-sm font-medium text-bg disabled:opacity-50">
              Ask
            </button>
          </form>
        </Panel>

        <Panel title="Try asking">
          <ul className="space-y-1.5">
            {SUGGESTIONS.map((s) => (
              <li key={s}>
                <button
                  type="button"
                  disabled={busy || disabled}
                  onClick={() => void send(s)}
                  className={cx("w-full rounded-md border border-line px-3 py-1.5 text-left text-xs text-muted hover:bg-panel-2 hover:text-text disabled:opacity-50")}
                >
                  {s}
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-4 space-y-1 text-[11px] text-faint">
            <p><span className="text-up">Verified data</span>: figures from tools</p>
            <p><span className="text-accent">Estimates</span>: indicators and scores</p>
            <p><span className="text-muted">Interpretation</span>: the model&apos;s opinion</p>
            <p><span className="text-warn">Missing information</span>: data that does not exist</p>
          </div>
        </Panel>
      </div>
    </>
  );
}
