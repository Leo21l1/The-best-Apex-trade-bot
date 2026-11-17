"use client";

import Link from "next/link";
import { useState } from "react";
import { cx } from "@/components/ui/primitives";
import { usePolling } from "@/hooks/usePolling";
import { formatEt } from "@/lib/format";

interface TickerItem { id: number; title: string; source_name: string; impact: string; published_at: string; detected_at: string; link: string | null; bias: string }
interface NewsResponse { items: TickerItem[]; sources: { key: string; ok: boolean | null; error: string | null; enabled: boolean }[] }

const IMPACT: Record<string, string> = {
  CRITICAL: "border-down/60 text-down", HIGH: "border-down/40 text-down", MODERATE: "border-warn/50 text-warn",
  LOW: "border-line text-muted", UNKNOWN: "border-line text-faint",
};

/**
 * Scrolling strip of the latest published announcements from the configured official feeds,
 * each with its source and publication time. Pauses on hover/focus; static when reduced motion is on.
 */
export function NewsTicker() {
  const { data, error } = usePolling<NewsResponse>("/api/news?hours=168&limit=20", 120_000);
  const [paused, setPaused] = useState(false);
  const items = data?.items ?? [];
  const enabled = data?.sources.filter((s) => s.enabled) ?? [];
  const failing = enabled.filter((s) => s.ok === false);
  if (error || !data) return null;
  if (!items.length) {
    return (
      <div className="border-b border-line bg-panel/60 px-4 py-1 text-[11px] text-faint" data-testid="news-ticker">
        {enabled.length ? `No announcements from the official feeds yet${failing.length ? ` (${failing.map((s) => s.key).join(", ")} unreachable)` : ""}.` : "News feeds disabled."}
      </div>
    );
  }
  const row = items.map((n) => (
    <span key={n.id} className="mr-8 inline-flex items-center gap-2 whitespace-nowrap">
      <span className={cx("rounded border px-1 font-mono text-[10px]", IMPACT[n.impact])}>{n.impact}</span>
      {n.link ? <a href={n.link} target="_blank" rel="noreferrer noopener" className="text-text hover:text-accent">{n.title}</a> : <span className="text-text">{n.title}</span>}
      <span className="text-faint">· {n.source_name.split(" (")[0]} · published {formatEt(n.published_at, true)}</span>
    </span>
  ));
  return (
    <div className="flex items-center gap-2 overflow-hidden border-b border-line bg-panel/60 py-1 text-[11px]" data-testid="news-ticker"
      onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)} onFocus={() => setPaused(true)} onBlur={() => setPaused(false)}>
      <Link href="/calendar" className="shrink-0 pl-4 font-mono text-[10px] tracking-wider text-accent">NEWS</Link>
      <div className="relative min-w-0 flex-1 overflow-hidden">
        <div className={cx("inline-block whitespace-nowrap motion-safe:animate-[ticker_90s_linear_infinite]", paused && "[animation-play-state:paused]")}>
          {row}<span aria-hidden>{row}</span>
        </div>
      </div>
      {failing.length > 0 && <span className="shrink-0 pr-4 text-warn" title={failing.map((s) => `${s.key}: ${s.error}`).join("\n")}>feed issue</span>}
    </div>
  );
}
