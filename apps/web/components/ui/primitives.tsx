import type { ComponentState, DataQuality } from "@apex/shared-types";
import { QUALITY_LABEL, STATE_LABEL } from "@/lib/format";

export function cx(...c: (string | false | null | undefined)[]): string {
  return c.filter(Boolean).join(" ");
}

export function Panel({
  title,
  actions,
  children,
  className,
}: {
  title?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cx("apex-panel min-w-0 rounded-xl", className)}>
      {title !== undefined && (
        <header className="flex items-center justify-between gap-3 border-b border-line/70 px-4 py-2.5">
          <h2 className="flex items-center gap-2 text-[12px] font-semibold tracking-[0.12em] text-muted uppercase">
            <span className="h-3 w-0.5 rounded-full bg-gradient-to-b from-accent to-accent-2" aria-hidden />
            {title}
          </h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const STATE_STYLE: Record<ComponentState, string> = {
  ok: "text-up border-up/30 bg-up/10",
  degraded: "text-warn border-warn/30 bg-warn/10",
  down: "text-down border-down/30 bg-down/10",
  not_configured: "text-muted border-line bg-panel-2",
};

const DOT: Record<ComponentState, string> = {
  ok: "bg-up",
  degraded: "bg-warn",
  down: "bg-down",
  not_configured: "bg-faint",
};

export function StatePill({ state, label }: { state: ComponentState; label?: string }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-medium",
        STATE_STYLE[state],
      )}
    >
      <span className={cx("h-1.5 w-1.5 rounded-full", DOT[state])} aria-hidden />
      {label ?? STATE_LABEL[state]}
    </span>
  );
}

const QUALITY_STYLE: Record<DataQuality, string> = {
  live: "text-up border-up/30",
  delayed: "text-warn border-warn/30",
  historical: "text-info border-accent/30",
  simulated: "text-accent border-accent/40 border-dashed",
  unavailable: "text-muted border-line",
};

/** Every price shown in the app must carry one of these badges. */
export function QualityBadge({ quality }: { quality: DataQuality }) {
  return (
    <span
      className={cx(
        "rounded border px-1.5 py-px font-mono text-[10px] tracking-wider",
        QUALITY_STYLE[quality],
      )}
    >
      {QUALITY_LABEL[quality]}
    </span>
  );
}

export function Callout({
  tone = "info",
  title,
  children,
}: {
  tone?: "info" | "warn" | "danger";
  title: string;
  children?: React.ReactNode;
}) {
  const style = {
    info: "border-accent/30 bg-accent/5",
    warn: "border-warn/40 bg-warn/5",
    danger: "border-down/40 bg-down/5",
  }[tone];
  const titleColor = { info: "text-accent", warn: "text-warn", danger: "text-down" }[tone];
  return (
    <div role={tone === "info" ? "note" : "alert"} className={cx("rounded-xl border px-4 py-3", style)}>
      <p className={cx("text-sm font-medium", titleColor)}>{title}</p>
      {children && <div className="mt-1 text-sm text-muted">{children}</div>}
    </div>
  );
}

export function EmptyState({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-line px-6 py-10 text-center">
      <p className="text-sm font-medium text-text">{title}</p>
      {children && <div className="mt-1.5 max-w-md text-sm text-muted">{children}</div>}
    </div>
  );
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border border-line bg-panel-2 px-1.5 py-px font-mono text-[11px] text-muted">
      {children}
    </kbd>
  );
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <p className="apex-label">{label}</p>
      <p className="mt-1 truncate font-mono text-lg font-semibold text-text">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-faint">{hint}</p>}
    </div>
  );
}
