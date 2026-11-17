import { describe, expect, it } from "vitest";
import { parseWatchlist, toggleSymbol } from "../watchlist";

describe("watchlist", () => {
  it("toggles symbols without mutating", () => {
    const a = ["ES"];
    expect(toggleSymbol(a, "NQ")).toEqual(["ES", "NQ"]);
    expect(toggleSymbol(a, "ES")).toEqual([]);
    expect(a).toEqual(["ES"]);
  });
  it("parses defensively", () => {
    expect(parseWatchlist('["ES","NQ"]')).toEqual(["ES", "NQ"]);
    expect(parseWatchlist(null)).toEqual([]);
    expect(parseWatchlist("{bad")).toEqual([]);
    expect(parseWatchlist('{"a":1}')).toEqual([]);
    expect(parseWatchlist('["ES", 5, "<script>", "toolongsymbol"]')).toEqual(["ES"]);
  });
});
