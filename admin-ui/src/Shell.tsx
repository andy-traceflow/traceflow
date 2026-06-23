import { useEffect, useState } from "react";
import { api, isDemo, type AdminMe, type ClientItem } from "./api";
import { CLIENT_STATUS_LABELS, CLIENT_TIER_LABELS, labelFor } from "./labels";
import ConfigPanel from "./panels/ConfigPanel";
import LeadsPanel from "./panels/LeadsPanel";
import ActivityPanel from "./panels/ActivityPanel";
import MappingsPanel from "./panels/MappingsPanel";
import UsageCard from "./panels/UsageCard";

const TABS = ["Leads", "Activity", "Config", "Mappings", "Usage"] as const;
type Tab = (typeof TABS)[number];

export default function Shell({ me, onLogout }: { me: AdminMe; onLogout: () => void }) {
  const [clients, setClients] = useState<ClientItem[]>([]);
  const [clientId, setClientId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("Leads");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<ClientItem[]>("/clients")
      .then((list) => {
        setClients(list);
        if (list.length > 0) setClientId((cur) => cur ?? list[0].id);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load clients"));
  }, []);

  const selected = clients.find((c) => c.id === clientId) ?? null;

  return (
    <div className="mx-auto min-h-screen w-full max-w-[1680px] px-6 lg:px-8 2xl:px-12 pb-16">
      {isDemo && (
        <div
          role="status"
          className="mt-3 flex flex-wrap items-center gap-2 rounded border border-signal/30 bg-signal/10 px-3 py-2 font-mono text-xs text-signal"
        >
          <span className="inline-block h-2 w-2 rounded-full bg-signal" aria-hidden="true" />
          <span className="font-semibold uppercase tracking-wider">Demo · read-only</span>
          <span className="text-signal/80">
            Sample data — this is a live portfolio preview of the TraceFlow admin console. Edits are disabled.
          </span>
        </div>
      )}
      <header className="flex flex-wrap items-center gap-3 border-b border-border py-3">
        <h1 className="font-mono text-base uppercase tracking-[0.2em] text-signal">
          TraceFlow Admin
        </h1>
        <select
          aria-label="Active client"
          value={clientId ?? ""}
          onChange={(e) => setClientId(e.target.value || null)}
          className="rounded border border-border bg-surface px-3 py-2.5 text-sm outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
        >
          {clients.length === 0 && <option value="">No clients</option>}
          {clients.map((c) => (
            <option key={c.id} value={c.id}>
              {c.business_name} · {labelFor(CLIENT_STATUS_LABELS, c.status)}
            </option>
          ))}
        </select>
        {selected && (
          <span className="font-mono text-xs text-zinc-400">
            {labelFor(CLIENT_TIER_LABELS, selected.tier)} · {selected.crm_provider ?? "no CRM"} · {selected.leads_30d}{" "}
            leads/30d
          </span>
        )}
        <div className="ml-auto flex items-center gap-3">
          <span className="font-mono text-xs text-zinc-400">{me.email}</span>
          {!isDemo && (
            <button
              onClick={onLogout}
              className="rounded border border-border px-3 py-2 font-mono text-sm text-zinc-400 hover:border-zinc-600 hover:text-zinc-200"
            >
              Log out
            </button>
          )}
        </div>
      </header>

      <nav
        role="tablist"
        aria-label="Admin sections"
        className="flex gap-1 py-3"
        onKeyDown={(e) => {
          if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
          e.preventDefault();
          const i = TABS.indexOf(tab);
          const next =
            e.key === "ArrowRight"
              ? (i + 1) % TABS.length
              : (i - 1 + TABS.length) % TABS.length;
          setTab(TABS[next]);
          e.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
        }}
      >
        {TABS.map((t) => (
          <button
            key={t}
            role="tab"
            id={`tab-${t}`}
            aria-selected={tab === t}
            aria-controls="tabpanel"
            tabIndex={tab === t ? 0 : -1}
            onClick={() => setTab(t)}
            className={`rounded px-4 py-2.5 font-mono text-sm uppercase tracking-wider ${
              tab === t
                ? "bg-signal text-zinc-950"
                : "text-zinc-400 hover:bg-surface hover:text-zinc-200"
            }`}
          >
            {t}
          </button>
        ))}
      </nav>

      {error && (
        <p role="alert" className="mb-4 rounded border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      {!clientId ? (
        <p className="py-12 text-center text-sm text-zinc-400">
          No clients yet. Add a client to get started.
        </p>
      ) : (
        <main id="tabpanel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
          {tab === "Leads" && <LeadsPanel clientId={clientId} />}
          {tab === "Activity" && <ActivityPanel clientId={clientId} />}
          {tab === "Config" && <ConfigPanel clientId={clientId} />}
          {tab === "Mappings" && <MappingsPanel clientId={clientId} />}
          {tab === "Usage" && <UsageCard clientId={clientId} />}
        </main>
      )}
    </div>
  );
}
