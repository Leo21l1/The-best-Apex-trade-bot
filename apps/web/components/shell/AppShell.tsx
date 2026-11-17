"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { createChordHandler, isEditableTarget } from "@/lib/hotkeys";
import { navByKey } from "@/lib/nav";
import { useSession } from "@/hooks/useSession";
import { CommandPalette } from "./CommandPalette";
import { ShortcutHelp } from "./ShortcutHelp";
import { Sidebar } from "./Sidebar";
import { NewsTicker } from "./NewsTicker";
import { TopBar } from "./TopBar";

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { user, loading } = useSession();
  const [navOpen, setNavOpen] = useState(false);
  const [palette, setPalette] = useState(false);
  const [help, setHelp] = useState(false);
  const handler = useMemo(() => createChordHandler(), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const action = handler({
        key: e.key,
        metaKey: e.metaKey,
        ctrlKey: e.ctrlKey,
        altKey: e.altKey,
        editable: isEditableTarget(e.target),
      });
      if (!action) return;
      if (action.type === "palette") {
        e.preventDefault();
        setPalette(true);
      } else if (action.type === "help") {
        setHelp(true);
      } else if (action.type === "escape") {
        setPalette(false);
        setHelp(false);
        setNavOpen(false);
      } else if (action.type === "navigate") {
        const item = navByKey(action.key);
        if (item) router.push(item.href);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handler, router]);

  if (loading || !user) {
    return (
      <div className="grid min-h-screen place-items-center text-sm text-faint" aria-busy="true">
        Checking session…
      </div>
    );
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar open={navOpen} onNavigate={() => setNavOpen(false)} />
      {navOpen && (
        <div className="fixed inset-0 z-30 bg-black/50 lg:hidden" onClick={() => setNavOpen(false)} />
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onMenu={() => setNavOpen(true)} onPalette={() => setPalette(true)} />
        <NewsTicker />
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-6">{children}</main>
        <footer className="border-t border-line px-6 py-3 text-[11px] text-faint">
          Analysis only. PROJECT APEX does not place, modify or cancel orders. Risk figures are
          advisory and cannot guarantee protection against account failure.
        </footer>
      </div>
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
      <ShortcutHelp open={help} onClose={() => setHelp(false)} />
    </div>
  );
}
