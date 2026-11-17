"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV, NAV_GROUPS } from "@/lib/nav";
import { cx, Kbd } from "@/components/ui/primitives";

export function Sidebar({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Primary"
      className={cx(
        "fixed inset-y-0 left-0 z-40 w-64 shrink-0 overflow-y-auto border-r border-line bg-panel transition-transform lg:static lg:translate-x-0",
        open ? "translate-x-0" : "-translate-x-full",
      )}
    >
      <div className="flex h-14 items-center gap-2 border-b border-line px-4">
        <span className="grid h-7 w-7 place-items-center rounded bg-accent/15 font-mono text-sm font-semibold text-accent">
          A
        </span>
        <div className="leading-tight">
          <p className="text-sm font-semibold tracking-wide">PROJECT APEX</p>
          <p className="text-[11px] text-faint">Trading intelligence</p>
        </div>
      </div>
      {NAV_GROUPS.map((group) => (
        <div key={group} className="px-2 pt-4">
          <p className="px-2 pb-1 text-[11px] font-medium tracking-wider text-faint uppercase">
            {group}
          </p>
          <ul>
            {NAV.filter((n) => n.group === group).map((n) => {
              const active = pathname === n.href;
              return (
                <li key={n.href}>
                  <Link
                    href={n.href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    className={cx(
                      "group flex items-center justify-between rounded-md px-2 py-1.5 text-sm",
                      active
                        ? "bg-panel-2 text-text"
                        : "text-muted hover:bg-panel-2/60 hover:text-text",
                    )}
                  >
                    <span>{n.label}</span>
                    <span className="hidden opacity-0 group-hover:opacity-100 lg:inline">
                      <Kbd>g {n.key}</Kbd>
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      <div className="h-6" />
    </nav>
  );
}
