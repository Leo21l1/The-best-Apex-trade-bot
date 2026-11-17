import type { CopilotToolCall } from "@apex/shared-types";
import { cx } from "@/components/ui/primitives";
import { type Inline, parseMarkdown, type Section } from "@/lib/markdown";

const SECTION_STYLE: Record<Section, string> = {
  verified: "text-up",
  estimates: "text-accent",
  interpretation: "text-muted",
  missing: "text-warn",
};

function Inlines({ parts }: { parts: Inline[] }) {
  return <>{parts.map((p, i) => (p.bold ? <strong key={i} className="font-semibold text-text">{p.text}</strong> : <span key={i}>{p.text}</span>))}</>;
}

export function AnswerBody({ text }: { text: string }) {
  return (
    <div className="space-y-2 text-sm leading-relaxed text-text/90">
      {parseMarkdown(text).map((b, i) => {
        if (b.type === "heading") {
          return (
            <h3 key={i} className={cx("pt-1 text-xs font-semibold tracking-wide uppercase", b.section ? SECTION_STYLE[b.section] : "text-muted")}>
              {b.text}
            </h3>
          );
        }
        if (b.type === "list") {
          return (
            <ul key={i} className="list-disc space-y-1 pl-5">
              {b.items.map((it, j) => <li key={j}><Inlines parts={it} /></li>)}
            </ul>
          );
        }
        return <p key={i}><Inlines parts={b.inlines} /></p>;
      })}
    </div>
  );
}

export function ToolTrace({ calls }: { calls: CopilotToolCall[] }) {
  if (calls.length === 0) {
    return <p className="mt-2 text-xs text-warn">No application data was consulted for this answer.</p>;
  }
  return (
    <details className="mt-3 rounded-md border border-line bg-bg/40" data-testid="tool-trace">
      <summary className="cursor-pointer px-3 py-1.5 text-xs text-muted">
        Data used: {calls.length} read-only tool call{calls.length === 1 ? "" : "s"} ({[...new Set(calls.map((c) => c.name))].join(", ")})
      </summary>
      <ul className="space-y-2 px-3 pb-3">
        {calls.map((c, i) => (
          <li key={i} className="text-xs">
            <p className={cx("font-mono", c.is_error ? "text-down" : "text-text")}>
              {c.name}({Object.keys(c.input).length ? JSON.stringify(c.input) : ""}){c.is_error ? " — error" : ""}
            </p>
            <pre className="mt-1 max-h-48 overflow-auto rounded bg-panel-2 p-2 font-mono text-[11px] text-muted">
              {JSON.stringify(c.result, null, 2)}
            </pre>
          </li>
        ))}
      </ul>
    </details>
  );
}
