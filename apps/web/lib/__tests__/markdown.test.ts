import { describe, expect, it } from "vitest";
import { parseInline, parseMarkdown, sectionOf } from "../markdown";

describe("markdown subset", () => {
  it("recognises the four answer sections", () => {
    expect(sectionOf("Verified data")).toBe("verified");
    expect(sectionOf("Estimates:")).toBe("estimates");
    expect(sectionOf("Interpretation")).toBe("interpretation");
    expect(sectionOf("Missing information")).toBe("missing");
    expect(sectionOf("Other")).toBeNull();
  });

  it("parses headings, paragraphs and lists", () => {
    const blocks = parseMarkdown("**Verified data**\n- ES last 5,000.25 (**simulated**)\n- Budget $1,000\n\nSome text\ncontinues.\n## Missing information\n1. No trades imported");
    expect(blocks.map((b) => b.type)).toEqual(["heading", "list", "paragraph", "heading", "list"]);
    expect(blocks[0]).toEqual({ type: "heading", text: "Verified data", section: "verified" });
    const list = blocks[1];
    if (list?.type !== "list") throw new Error("expected list");
    expect(list.items[0]).toEqual([
      { text: "ES last 5,000.25 (", bold: false }, { text: "simulated", bold: true }, { text: ")", bold: false },
    ]);
    const p = blocks[2];
    if (p?.type !== "paragraph") throw new Error("expected paragraph");
    expect(p.inlines[0]!.text).toBe("Some text continues.");
  });

  it("never produces HTML", () => {
    const blocks = parseMarkdown("<img src=x onerror=alert(1)> **<b>x</b>**");
    expect(JSON.stringify(blocks)).toContain("<img src=x onerror=alert(1)>");
    expect(parseInline("a ** b")).toEqual([{ text: "a ** b", bold: false }]);
  });
});
