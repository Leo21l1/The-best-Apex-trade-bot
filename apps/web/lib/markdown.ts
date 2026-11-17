/**
 * Minimal, safe Markdown subset for copilot answers. Produces a block tree that React renders as
 * text nodes, so model output can never inject HTML.
 */
export type Inline = { text: string; bold: boolean };
export type Block =
  | { type: "heading"; text: string; section: Section | null }
  | { type: "paragraph"; inlines: Inline[] }
  | { type: "list"; items: Inline[][] };

export type Section = "verified" | "estimates" | "interpretation" | "missing";

const SECTIONS: [RegExp, Section][] = [
  [/^verified data$/i, "verified"],
  [/^estimates?$/i, "estimates"],
  [/^interpretation$/i, "interpretation"],
  [/^missing information$/i, "missing"],
];

export function sectionOf(heading: string): Section | null {
  const h = heading.trim().replace(/:$/, "");
  return SECTIONS.find(([rx]) => rx.test(h))?.[1] ?? null;
}

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  for (const p of parts) {
    if (!p) continue;
    if (p.startsWith("**") && p.endsWith("**") && p.length > 4) out.push({ text: p.slice(2, -2), bold: true });
    else out.push({ text: p, bold: false });
  }
  return out;
}

export function parseMarkdown(src: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: Inline[][] | null = null;
  const flushPara = () => {
    if (para.length) blocks.push({ type: "paragraph", inlines: parseInline(para.join(" ")) });
    para = [];
  };
  const flushList = () => {
    if (list) blocks.push({ type: "list", items: list });
    list = null;
  };
  for (const raw of src.replace(/\r\n/g, "\n").split("\n")) {
    const line = raw.trim();
    const heading = line.match(/^(?:#{1,4}\s+(.+)|\*\*([^*]+)\*\*:?)$/);
    const bullet = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (!line) {
      flushPara();
      flushList();
    } else if (heading) {
      flushPara();
      flushList();
      const text = (heading[1] ?? heading[2] ?? "").trim();
      blocks.push({ type: "heading", text, section: sectionOf(text) });
    } else if (bullet) {
      flushPara();
      list = list ?? [];
      list.push(parseInline(bullet[1] ?? ""));
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return blocks;
}
