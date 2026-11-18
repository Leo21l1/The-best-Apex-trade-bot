/** Journal math for manually entered trades (same rules as the server; the server is authoritative). */

export const POINT_VALUE: Record<string, number> = { ES: 50, MES: 5, NQ: 20, MNQ: 2 };
export const TICK: Record<string, number> = { ES: 0.25, MES: 0.25, NQ: 0.25, MNQ: 0.25 };

export interface FillRow { time: string; side: "entry" | "exit"; qty: number; price: number }

export interface PnlPreview {
  balanced: boolean; qty: number; avgEntry: number | null; avgExit: number | null;
  points: number | null; gross: number | null; net: number | null; r: number | null; error: string | null;
}

/** Quantity-weighted averages; gross = (avg exit - avg entry) x direction x point value x quantity. */
export function previewPnl(symbol: string, direction: "long" | "short", fills: FillRow[], fees: number, stop?: number | null): PnlPreview {
  const pv = POINT_VALUE[symbol];
  const ent = fills.filter((f) => f.side === "entry" && f.qty > 0 && f.price > 0);
  const ex = fills.filter((f) => f.side === "exit" && f.qty > 0 && f.price > 0);
  const qe = ent.reduce((a, f) => a + f.qty, 0), qx = ex.reduce((a, f) => a + f.qty, 0);
  const empty = { balanced: false, qty: qe, avgEntry: null, avgExit: null, points: null, gross: null, net: null, r: null };
  if (!pv) return { ...empty, error: `Unknown symbol ${symbol}` };
  if (!ent.length || !ex.length) return { ...empty, error: "Add at least one entry and one exit fill." };
  if (qe !== qx) return { ...empty, error: `Entry quantity ${qe} must equal exit quantity ${qx}.` };
  const t = TICK[symbol] ?? 0.25;
  const off = fills.find((f) => f.price > 0 && Math.abs(Math.round(f.price / t) * t - f.price) > 1e-9);
  if (off) return { ...empty, error: `Price ${off.price} is not on the ${t} tick grid.` };
  const avgEntry = ent.reduce((a, f) => a + f.qty * f.price, 0) / qe;
  const avgExit = ex.reduce((a, f) => a + f.qty * f.price, 0) / qx;
  const s = direction === "long" ? 1 : -1;
  const gross = Math.round((avgExit - avgEntry) * s * pv * qe * 100) / 100;
  const net = Math.round((gross - fees) * 100) / 100;
  const risk = stop ? Math.abs(avgEntry - stop) * pv * qe : 0;
  return { balanced: true, qty: qe, avgEntry, avgExit, points: Math.round((avgExit - avgEntry) * s * 1e6) / 1e6, gross, net, r: risk > 0 ? Math.round((net / risk) * 100) / 100 : null, error: null };
}

/** "YYYY-MM-DDTHH:mm" New York wall time -> ISO instant with the correct EST/EDT offset. */
export function etLocalToIso(local: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(local);
  if (!m) return null;
  const [y, mo, d, h, mi] = m.slice(1).map(Number) as [number, number, number, number, number];
  const guess = Date.UTC(y, mo - 1, d, h, mi);
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
    .formatToParts(new Date(guess));
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value);
  const asEt = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"));
  const offset = asEt - guess; // ET minus UTC, e.g. -5h
  return new Date(guess - offset).toISOString();
}
