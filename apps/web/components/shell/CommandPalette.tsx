"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { filterNav } from "@/lib/nav";
import { cx, Kbd } from "@/components/ui/primitives";

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  // Mounting only while open gives every opening a fresh query/selection.
  return open ? <Palette onClose={onClose} /> : null;
}

function Palette({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const results = useMemo(() => filterNav(query), [query]);

  const go = (href: string) => {
    onClose();
    router.push(href);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 px-4 pt-[12vh]"
      onMouseDown={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="w-full max-w-lg overflow-hidden rounded-lg border border-line bg-panel shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <input
          autoFocus
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setIndex(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setIndex((i) => Math.min(i + 1, results.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setIndex((i) => Math.max(i - 1, 0));
            } else if (e.key === "Enter" && results[index]) {
              go(results[index].href);
            } else if (e.key === "Escape") {
              onClose();
            }
          }}
          placeholder="Jump to page…"
          aria-label="Search pages"
          className="w-full border-b border-line bg-transparent px-4 py-3 text-sm outline-none placeholder:text-faint"
        />
        <ul className="max-h-80 overflow-y-auto py-1" role="listbox">
          {results.length === 0 && <li className="px-4 py-3 text-sm text-faint">No matches</li>}
          {results.map((r, i) => (
            <li key={r.href} role="option" aria-selected={i === index}>
              <button
                type="button"
                onMouseEnter={() => setIndex(i)}
                onClick={() => go(r.href)}
                className={cx(
                  "flex w-full items-center justify-between gap-3 px-4 py-2 text-left",
                  i === index && "bg-panel-2",
                )}
              >
                <span>
                  <span className="block text-sm text-text">{r.label}</span>
                  <span className="block text-xs text-faint">{r.summary}</span>
                </span>
                <Kbd>g {r.key}</Kbd>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
