"use client";

import type { BootstrapStatus, SessionOut } from "@apex/shared-types";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

export function LoginForm() {
  const router = useRouter();
  const [mode, setMode] = useState<"loading" | "login" | "bootstrap">("loading");
  const [tokenRequired, setTokenRequired] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [bootstrapToken, setBootstrapToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<BootstrapStatus>("/api/auth/bootstrap-status")
      .then((s) => {
        setTokenRequired(s.bootstrap_token_required);
        setMode(s.needs_bootstrap ? "bootstrap" : "login");
      })
      .catch(() => {
        setError("API unreachable. Is the backend running?");
        setMode("login");
      });
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (mode === "bootstrap" && password !== confirm) {
      setError("Passwords do not match");
      return;
    }
    setBusy(true);
    try {
      if (mode === "bootstrap") {
        await api<SessionOut>("/api/auth/bootstrap", {
          method: "POST",
          body: { email, password, bootstrap_token: bootstrapToken || null },
        });
      } else {
        await api<SessionOut>("/api/auth/login", { method: "POST", body: { email, password } });
      }
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unexpected error");
    } finally {
      setBusy(false);
    }
  };

  const input =
    "mt-1 w-full rounded-md border border-line bg-panel-2 px-3 py-2 text-sm outline-none focus:border-accent";

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <span className="mx-auto grid h-10 w-10 place-items-center rounded-md bg-accent/15 font-mono font-semibold text-accent">
            A
          </span>
          <h1 className="mt-3 text-lg font-semibold tracking-wide">PROJECT APEX</h1>
          <p className="text-xs text-faint">Analysis-only trading intelligence</p>
        </div>
        <form onSubmit={submit} className="rounded-lg border border-line bg-panel p-5" noValidate>
          <h2 className="mb-4 text-sm font-medium">
            {mode === "bootstrap" ? "Create administrator account" : "Sign in"}
          </h2>
          {mode === "bootstrap" && (
            <p className="mb-4 text-xs text-muted">
              First run: no users exist yet. This form is disabled permanently once an account is
              created. Use at least 12 characters with three of: lowercase, uppercase, digit,
              symbol.
            </p>
          )}
          <label className="block text-xs text-muted">
            Email
            <input
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={input}
            />
          </label>
          <label className="mt-3 block text-xs text-muted">
            Password
            <input
              type="password"
              autoComplete={mode === "bootstrap" ? "new-password" : "current-password"}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={input}
            />
          </label>
          {mode === "bootstrap" && (
            <label className="mt-3 block text-xs text-muted">
              Confirm password
              <input
                type="password"
                autoComplete="new-password"
                required
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className={input}
              />
            </label>
          )}
          {mode === "bootstrap" && tokenRequired && (
            <label className="mt-3 block text-xs text-muted">
              Bootstrap token (APEX_BOOTSTRAP_TOKEN)
              <input
                type="password"
                autoComplete="off"
                value={bootstrapToken}
                onChange={(e) => setBootstrapToken(e.target.value)}
                className={input}
              />
            </label>
          )}
          {error && (
            <p role="alert" className="mt-3 text-sm text-down">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={busy || mode === "loading"}
            className="mt-5 w-full rounded-md bg-accent px-3 py-2 text-sm font-medium text-bg hover:bg-accent/90 disabled:opacity-50"
          >
            {busy ? "Please wait…" : mode === "bootstrap" ? "Create account" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
