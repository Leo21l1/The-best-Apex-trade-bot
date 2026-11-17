"use client";

import { useSyncExternalStore } from "react";
import { formatEt } from "@/lib/format";

function subscribe(cb: () => void) {
  const t = setInterval(cb, 1000);
  return () => clearInterval(t);
}
const getSecond = () => Math.floor(Date.now() / 1000);
const getServerSnapshot = () => 0;

export function Clock() {
  const sec = useSyncExternalStore(subscribe, getSecond, getServerSnapshot);
  return (
    <span className="font-mono text-xs text-muted tabular-nums" aria-label="New York time">
      {sec ? formatEt(new Date(sec * 1000)) : "--:--:-- ET"}
    </span>
  );
}
