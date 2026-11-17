/**
 * Keyboard chord state machine ("g" then a key) — pure and unit-tested.
 */
export type HotkeyAction =
  | { type: "navigate"; key: string }
  | { type: "palette" }
  | { type: "help" }
  | { type: "escape" }
  | null;

export interface KeyLike {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  /** True when the event originates from an editable element. */
  editable?: boolean;
}

export const CHORD_TIMEOUT_MS = 1200;

export function createChordHandler(now: () => number = Date.now) {
  let pendingSince: number | null = null;
  return function handle(e: KeyLike): HotkeyAction {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") return { type: "palette" };
    if (e.key === "Escape") {
      pendingSince = null;
      return { type: "escape" };
    }
    if (e.editable || e.metaKey || e.ctrlKey || e.altKey) return null;
    const t = now();
    if (pendingSince !== null && t - pendingSince <= CHORD_TIMEOUT_MS) {
      pendingSince = null;
      return { type: "navigate", key: e.key.toLowerCase() };
    }
    pendingSince = null;
    if (e.key === "g") {
      pendingSince = t;
      return null;
    }
    if (e.key === "/") return { type: "palette" };
    if (e.key === "?") return { type: "help" };
    return null;
  };
}

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!target || typeof (target as HTMLElement).tagName !== "string") return false;
  const el = target as HTMLElement;
  return (
    el.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)
  );
}
