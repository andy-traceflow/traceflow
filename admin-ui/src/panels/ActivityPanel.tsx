import { useEffect, useState } from "react";
import { api, type RoutingActivity, type RoutingLogItem } from "../api";

const BUCKET_LABELS: Record<string, string> = {
  potential_lead: "Genuine leads",
  existing_customer: "Existing customers",
  known_non_lead: "Vendors / non-leads",
  spam: "Spam",
  active_conversation: "Mid-conversation",
};

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });

export default function ActivityPanel({ clientId }: { clientId: string }) {
  const [windowDays, setWindowDays] = useState(30);
  const [activity, setActivity] = useState<RoutingActivity | null>(null);
  const [log, setLog] = useState<RoutingLogItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setActivity(null);
    setError(null);
    api<RoutingActivity>(`/clients/${clientId}/routing-activity?window_days=${windowDays}`)
      .then(setActivity)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load activity"));
  }, [clientId, windowDays]);

  useEffect(() => {
    setLog(null);
    api<RoutingLogItem[]>(`/clients/${clientId}/routing-log`)
      .then(setLog)
      .catch(() => setLog([]));
  }, [clientId]);

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold text-zinc-100">Routing activity</h2>
        <select
          aria-label="Activity window"
          value={windowDays}
          onChange={(e) => setWindowDays(Number(e.target.value))}
          className="rounded border border-border bg-surface px-2 py-1.5 font-mono text-xs outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
        >
          {[7, 30, 90].map((d) => (
            <option key={d} value={d}>
              last {d} days
            </option>
          ))}
        </select>
        <span className="font-mono text-xs text-zinc-400">
          recovery rate is computed over genuine leads only — this is the denominator
        </span>
      </div>

      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      {!activity && !error && <p className="font-mono text-sm text-zinc-400">loading…</p>}

      {activity && (
        <>
          <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Total missed calls" value={String(activity.total_calls)} accent />
            {Object.entries(BUCKET_LABELS).map(([key, label]) => (
              <Stat
                key={key}
                label={label}
                value={String(activity.breakdown[key] ?? 0)}
              />
            ))}
          </div>
          <div className="flex gap-6 font-mono text-sm">
            <span className="text-success">
              genuine-lead rate: {(activity.genuine_lead_rate * 100).toFixed(1)}%
            </span>
            <span className="text-danger">
              spam rate: {(activity.spam_rate * 100).toFixed(1)}%
            </span>
          </div>
        </>
      )}

      <div>
        <h3 className="mb-2 font-mono text-xs uppercase tracking-[0.15em] text-signal">
          Recent routing decisions
        </h3>
        {!log ? (
          <p className="font-mono text-sm text-zinc-400">loading…</p>
        ) : log.length === 0 ? (
          <p className="text-sm text-zinc-400">No routing events yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-left text-sm">
              <thead className="bg-surface font-mono text-xs uppercase tracking-wider text-zinc-400">
                <tr>
                  <th className="px-3 py-2">When</th>
                  <th className="px-3 py-2">Decision</th>
                  <th className="px-3 py-2">Caller</th>
                  <th className="px-3 py-2">Reason</th>
                  <th className="px-3 py-2">Event</th>
                </tr>
              </thead>
              <tbody>
                {log.map((item, i) => (
                  <tr key={`${item.created_at}-${item.event_type}-${i}`} className="border-t border-border/70">
                    <td className="px-3 py-2 font-mono text-xs text-zinc-400">
                      {fmtDate(item.created_at)}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-zinc-200">
                      {item.routing_decision ?? "—"}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-zinc-400">
                      {item.caller ?? "—"}
                    </td>
                    <td className="px-3 py-2 text-xs text-zinc-400">{item.reason ?? "—"}</td>
                    <td className="px-3 py-2 font-mono text-xs text-zinc-400">
                      {item.event_type}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-lg border border-border bg-surface/40 px-3 py-2">
      <div className={`text-2xl font-bold ${accent ? "text-signal" : "text-zinc-100"}`}>
        {value}
      </div>
      <div className="font-mono text-xs uppercase tracking-wider text-zinc-400">
        {label}
      </div>
    </div>
  );
}
