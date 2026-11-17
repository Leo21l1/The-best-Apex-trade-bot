"use client";

import type { ManagedUser } from "@apex/shared-types";
import { useCallback, useEffect, useState } from "react";
import { cx, Panel } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { formatEt } from "@/lib/format";

const input = "w-full rounded-md border border-line bg-panel-2 px-2.5 py-1.5 text-sm outline-none focus:border-accent";

export function ChangePassword() {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (next !== confirm) {
      setMsg({ ok: false, text: "New passwords do not match" });
      return;
    }
    try {
      await api("/api/auth/password", { method: "POST", body: { current_password: cur, new_password: next } });
      setMsg({ ok: true, text: "Password changed. Other sessions were signed out." });
      setCur(""); setNext(""); setConfirm("");
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Failed" });
    }
  };
  return (
    <Panel title="Change password">
      <form onSubmit={submit} className="space-y-2" noValidate>
        <label className="block text-xs text-muted">Current password<input type="password" autoComplete="current-password" className={input} value={cur} onChange={(e) => setCur(e.target.value)} /></label>
        <label className="block text-xs text-muted">New password<input type="password" autoComplete="new-password" className={input} value={next} onChange={(e) => setNext(e.target.value)} /></label>
        <label className="block text-xs text-muted">Confirm new password<input type="password" autoComplete="new-password" className={input} value={confirm} onChange={(e) => setConfirm(e.target.value)} /></label>
        <button type="submit" disabled={!cur || !next} className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-bg disabled:opacity-50">Change password</button>
        {msg && <p role="status" className={cx("text-sm", msg.ok ? "text-up" : "text-down")}>{msg.text}</p>}
        <p className="text-[11px] text-faint">At least 12 characters with three of: lowercase, uppercase, digit, symbol.</p>
      </form>
    </Panel>
  );
}

export function Users({ selfEmail }: { selfEmail: string | undefined }) {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"viewer" | "admin">("viewer");
  const [msg, setMsg] = useState<string | null>(null);
  const load = useCallback(async () => setUsers(await api<ManagedUser[]>("/api/users").catch(() => [])), []);
  useEffect(() => {
    // load() sets state only after its await resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setMsg(null);
    try {
      await api("/api/users", { method: "POST", body: { email, password, role } });
      setEmail(""); setPassword("");
      setMsg("User created.");
      void load();
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : "Failed");
    }
  };
  const toggle = async (u: ManagedUser) => {
    setMsg(null);
    try {
      await api(`/api/users/${u.id}`, { method: "PATCH", body: { is_active: !u.is_active } });
      void load();
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : "Failed");
    }
  };
  return (
    <Panel title="Users">
      <ul className="mb-4 space-y-1.5 text-sm" data-testid="users">
        {users.map((u) => (
          <li key={u.id} className="flex flex-wrap items-center justify-between gap-2">
            <span className={u.is_active ? "" : "text-faint line-through"}>{u.email} <span className="text-xs text-muted">({u.role})</span></span>
            <span className="flex items-center gap-3 text-xs text-faint">
              {u.last_login_at ? `last sign-in ${formatEt(u.last_login_at, true)}` : "never signed in"}
              {u.email !== selfEmail && (
                <button type="button" className="text-accent hover:underline" onClick={() => void toggle(u)}>{u.is_active ? "Deactivate" : "Reactivate"}</button>
              )}
            </span>
          </li>
        ))}
      </ul>
      <form onSubmit={create} className="grid gap-2 sm:grid-cols-[1fr_1fr_7rem_auto]">
        <input aria-label="New user email" type="email" placeholder="email" className={input} value={email} onChange={(e) => setEmail(e.target.value)} />
        <input aria-label="New user password" type="password" autoComplete="new-password" placeholder="initial password" className={input} value={password} onChange={(e) => setPassword(e.target.value)} />
        <select aria-label="New user role" className={input} value={role} onChange={(e) => setRole(e.target.value as typeof role)}>
          <option value="viewer">Viewer</option><option value="admin">Admin</option>
        </select>
        <button type="submit" disabled={!email || !password} className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-bg disabled:opacity-50">Add</button>
      </form>
      {msg && <p className="mt-2 text-sm text-muted">{msg}</p>}
      <p className="mt-2 text-[11px] text-faint">Viewers can see everything and run read-only risk checks and copilot questions, but cannot change data.</p>
    </Panel>
  );
}
