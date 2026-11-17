"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/** Fetch a JSON endpoint now and every `intervalMs`. `path` null disables it. */
export function usePolling<T>(path: string | null, intervalMs: number) {
  const [state, setState] = useState<{ data: T | null; error: string | null; path: string | null }>(
    { data: null, error: null, path: null },
  );
  useEffect(() => {
    if (!path) return;
    const ctrl = new AbortController();
    const load = () =>
      api<T>(path, { signal: ctrl.signal })
        .then((data) => setState({ data, error: null, path }))
        .catch((e: unknown) => {
          if (e instanceof DOMException && e.name === "AbortError") return;
          setState((p) => ({ ...p, error: (e as Error).message, path }));
        });
    void load();
    const t = setInterval(load, intervalMs);
    return () => {
      ctrl.abort();
      clearInterval(t);
    };
  }, [path, intervalMs]);
  // Never show a previous path's data for a new path.
  const current = state.path === path;
  return { data: current ? state.data : null, error: current ? state.error : null };
}
