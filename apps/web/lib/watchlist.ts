/** Per-browser watchlist persistence. Pure helpers plus guarded localStorage access. */
const KEY = "apex.watchlist.v1";

export function toggleSymbol(list: string[], symbol: string): string[] {
  return list.includes(symbol) ? list.filter((s) => s !== symbol) : [...list, symbol];
}

export function parseWatchlist(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const v: unknown = JSON.parse(raw);
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && /^[A-Z0-9]{1,8}$/.test(x)) : [];
  } catch {
    return [];
  }
}

export function loadWatchlist(): string[] {
  try {
    return parseWatchlist(window.localStorage.getItem(KEY));
  } catch {
    return [];
  }
}

export function saveWatchlist(list: string[]): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    /* storage unavailable: watchlist stays in memory */
  }
}
