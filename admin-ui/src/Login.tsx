import { useState, type FormEvent } from "react";
import { api, setToken, type AdminMe, type LoginResponse } from "./api";

export default function Login({ onLoggedIn }: { onLoggedIn: (me: AdminMe) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const resp = await api<LoginResponse>("/login", {
        method: "POST",
        body: { email, password },
      });
      setToken(resp.access_token);
      onLoggedIn(resp.admin);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4">
        <div>
          <div className="font-mono text-xs uppercase tracking-[0.2em] text-signal">
            TraceFlow
          </div>
          <h1 className="mt-1 text-xl font-semibold text-zinc-100">Admin console</h1>
        </div>
        <label className="block">
          <span className="font-mono text-xs uppercase tracking-wider text-zinc-500">
            Email
          </span>
          <input
            type="email"
            required
            autoFocus
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-signal"
          />
        </label>
        <label className="block">
          <span className="font-mono text-xs uppercase tracking-wider text-zinc-500">
            Password
          </span>
          <input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-signal"
          />
        </label>
        {error && (
          <p className="rounded border border-red-900 bg-red-950/50 px-3 py-2 text-sm text-red-400">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy}
          className="w-full rounded bg-signal px-3 py-2 text-sm font-semibold text-zinc-950 hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
