import type { ReplayData } from "@apex/shared-types";

/** What a trader could have seen at replay cursor ``i`` (pure; unit-tested for no look-ahead). */
export function replayView(d: ReplayData, i: number) {
  const idx = Math.max(0, Math.min(i, d.bars.length - 1));
  const bars = d.bars.slice(0, idx + 1);
  const now = bars[bars.length - 1]?.t ?? 0;
  const barEnd = now + 60; // a 1-minute bar is complete one minute after it opens
  const inTrade = now >= d.entry.t && now < d.exit.t;
  const afterEntry = now >= d.entry.t;
  const finished = idx >= d.bars.length - 1;
  return {
    bars,
    now,
    afterEntry,
    inTrade,
    exited: barEnd > d.exit.t,
    finished,
    knownEvents: d.events.filter((e) => e.known_from <= barEnd),
    pastEvents: d.events.filter((e) => e.t <= now),
    signal: afterEntry ? d.signal_at_entry : null,
    market: afterEntry ? d.market_at_entry : null,
  };
}

export function entryIndex(d: ReplayData): number {
  const i = d.bars.findIndex((b) => b.t >= d.entry.t - 59);
  return i < 0 ? 0 : i;
}
