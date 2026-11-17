"use client";

import { NAV } from "@/lib/nav";
import { Kbd } from "@/components/ui/primitives";

export function ShortcutHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 px-4" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Keyboard shortcuts"
        className="w-full max-w-md rounded-lg border border-line bg-panel p-5"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h2 className="text-sm font-semibold">Keyboard shortcuts</h2>
        <ul className="mt-3 space-y-1.5 text-sm">
          <li className="flex justify-between"><span className="text-muted">Command palette</span><span><Kbd>Ctrl K</Kbd> <Kbd>/</Kbd></span></li>
          <li className="flex justify-between"><span className="text-muted">This help</span><Kbd>?</Kbd></li>
          {NAV.map((n) => (
            <li key={n.href} className="flex justify-between">
              <span className="text-muted">{n.label}</span>
              <Kbd>g {n.key}</Kbd>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
