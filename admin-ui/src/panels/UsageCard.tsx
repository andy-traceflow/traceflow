import { useCallback, useEffect, useState } from "react";
import { api, type AIUsage } from "../api";

export default function UsageCard({ clientId }: { clientId: string }) {
  const [usage, setUsage] = useState<AIUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError(null);
    api<AIUsage>(`/clients/${clientId}/ai-usage`)
      .then(setUsage)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load usage"));
  }, [clientId]);

  useEffect(load, [load]);

  async function reset() {
    if (!confirm("Reset this month's AI interaction counter to 0?")) return;
    setBusy(true);
    try {
      const updated = await api<AIUsage>(`/clients/${clientId}/ai-usage/reset`, {
        method: "POST",
      });
      setUsage(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reset failed");
    } finally {
      setBusy(false);
    }
  }

  if (error) return <p role="alert" className="text-sm text-danger">{error}</p>;
  if (!usage) return <p className="font-mono text-sm text-zinc-400">Loading…</p>;

  const pct = Math.min(usage.percent_used, 100);
  const barTone =
    pct >= 90 ? "bg-danger" : pct >= 70 ? "bg-warning" : "bg-success";

  return (
    <div className="max-w-md rounded-lg border border-border bg-surface/40 p-5">
      <h2 className="font-mono text-xs uppercase tracking-[0.15em] text-signal">
        AI usage this period
      </h2>
      <div className="mt-3 flex items-baseline gap-2">
        <span className="text-3xl font-bold text-zinc-100">{usage.used}</span>
        <span className="font-mono text-sm text-zinc-400">/ {usage.cap} interactions</span>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded bg-surface-raised">
        <div
          className={`h-full origin-left ${barTone} transition-transform duration-500 ease-out`}
          style={{ transform: `scaleX(${pct / 100})` }}
        />
      </div>
      <div className="mt-2 flex items-center justify-between font-mono text-xs text-zinc-400">
        <span>{usage.percent_used.toFixed(1)}% used</span>
        <span>resets {new Date(usage.resets_at).toLocaleDateString()}</span>
      </div>
      <button
        onClick={reset}
        disabled={busy}
        className="mt-4 rounded border border-border-strong px-3 py-1.5 font-mono text-xs text-zinc-300 hover:border-zinc-500 disabled:opacity-40"
      >
        {busy ? "Resetting…" : "Reset counter"}
      </button>
      <p className="mt-2 text-xs text-zinc-400">
        Cap is set in Config → Operations.
      </p>
    </div>
  );
}
