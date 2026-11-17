import { describe, expect, it } from "vitest";
import { CHORD_TIMEOUT_MS, createChordHandler } from "../hotkeys";

describe("chord handler", () => {
  it("navigates on g + key within timeout", () => {
    let t = 0;
    const h = createChordHandler(() => t);
    expect(h({ key: "g" })).toBeNull();
    t = 300;
    expect(h({ key: "r" })).toEqual({ type: "navigate", key: "r" });
  });

  it("drops the chord after the timeout", () => {
    let t = 0;
    const h = createChordHandler(() => t);
    h({ key: "g" });
    t = CHORD_TIMEOUT_MS + 1;
    expect(h({ key: "r" })).toBeNull();
  });

  it("ignores keys typed into inputs", () => {
    const h = createChordHandler(() => 0);
    expect(h({ key: "g", editable: true })).toBeNull();
    expect(h({ key: "r", editable: true })).toBeNull();
    expect(h({ key: "/", editable: true })).toBeNull();
  });

  it("opens the palette with Ctrl/Cmd+K even from inputs", () => {
    const h = createChordHandler(() => 0);
    expect(h({ key: "k", ctrlKey: true, editable: true })).toEqual({ type: "palette" });
    expect(h({ key: "K", metaKey: true })).toEqual({ type: "palette" });
  });

  it("supports help and escape", () => {
    const h = createChordHandler(() => 0);
    expect(h({ key: "?" })).toEqual({ type: "help" });
    h({ key: "g" });
    expect(h({ key: "Escape" })).toEqual({ type: "escape" });
    expect(h({ key: "r" })).toBeNull();
  });
});
