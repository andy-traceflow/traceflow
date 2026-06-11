import { useCallback, useEffect, useState } from "react";
import {
  api,
  type ConversationMessage,
  type LeadDetail,
  type LeadItem,
  type LeadList,
} from "../api";

const CLASSIFICATIONS = ["potential_lead", "existing_customer", "known_non_lead", "spam", "all"];

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
const money = (n: number | null) =>
  n === null ? "—" : `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

export default function LeadsPanel({ clientId }: { clientId: string }) {
  const [classification, setClassification] = useState("potential_lead");
  const [includeTest, setIncludeTest] = useState(false);
  const [leads, setLeads] = useState<LeadList | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api<LeadList>(
      `/clients/${clientId}/leads?classification=${classification}&include_test=${includeTest}`,
    )
      .then(setLeads)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load leads"));
  }, [clientId, classification, includeTest]);

  useEffect(() => {
    setSelectedId(null);
    load();
  }, [load]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={classification}
          onChange={(e) => setClassification(e.target.value)}
          className="rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 font-mono text-xs outline-none focus:border-signal"
        >
          {CLASSIFICATIONS.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 font-mono text-xs text-zinc-400">
          <input
            type="checkbox"
            checked={includeTest}
            onChange={(e) => setIncludeTest(e.target.checked)}
            className="accent-[#3b82f6]"
          />
          include test leads
        </label>
        {leads && (
          <span className="ml-auto font-mono text-xs text-zinc-500">
            {leads.count} total
          </span>
        )}
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}
      {!leads && !error && <p className="font-mono text-sm text-zinc-500">loading…</p>}

      {leads && leads.data.length === 0 && (
        <p className="py-8 text-center text-sm text-zinc-500">
          No {classification === "all" ? "" : classification.replace("_", " ")} leads yet.
        </p>
      )}

      {leads && leads.data.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-zinc-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-zinc-900 font-mono text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="px-3 py-2">When</th>
                <th className="px-3 py-2">Contact</th>
                <th className="px-3 py-2">Service</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Outcome</th>
                <th className="px-3 py-2">CRM</th>
                <th className="px-3 py-2">Msgs</th>
              </tr>
            </thead>
            <tbody>
              {leads.data.map((l) => (
                <LeadRow key={l.id} lead={l} onOpen={() => setSelectedId(l.id)} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selectedId && (
        <LeadDrawer
          clientId={clientId}
          leadId={selectedId}
          onClose={() => setSelectedId(null)}
          onChanged={load}
        />
      )}
    </div>
  );
}

function LeadRow({ lead, onOpen }: { lead: LeadItem; onOpen: () => void }) {
  return (
    <tr
      onClick={onOpen}
      className="cursor-pointer border-t border-zinc-800/70 hover:bg-zinc-900/70"
    >
      <td className="px-3 py-2 font-mono text-xs text-zinc-400">{fmtDate(lead.created_at)}</td>
      <td className="px-3 py-2">
        <div className="text-zinc-200">{lead.contact_name ?? "Unknown caller"}</div>
        <div className="font-mono text-xs text-zinc-500">{lead.phone}</div>
      </td>
      <td className="px-3 py-2 text-zinc-300">
        {lead.service_type ?? "—"}
        {lead.budget_range && (
          <span className="ml-1 font-mono text-xs text-zinc-500">({lead.budget_range})</span>
        )}
      </td>
      <td className="px-3 py-2">
        <StatusBadge value={lead.qualification_status} />
        {lead.is_test && (
          <span className="ml-1 rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
            TEST
          </span>
        )}
      </td>
      <td className="px-3 py-2 font-mono text-xs">
        {lead.outcome === "won" ? (
          <span className="text-emerald-400">won {money(lead.recovered_value)}</span>
        ) : (
          <span className="text-zinc-500">{lead.outcome}</span>
        )}
      </td>
      <td className="px-3 py-2 font-mono text-xs">
        {lead.external_id ? (
          <span className="text-emerald-400">pushed</span>
        ) : (
          <span className="text-zinc-600">—</span>
        )}
      </td>
      <td className="px-3 py-2 font-mono text-xs text-zinc-400">{lead.message_count}</td>
    </tr>
  );
}

function StatusBadge({ value }: { value: string }) {
  const tone =
    value === "qualified" || value === "high_value"
      ? "border-emerald-800 text-emerald-400"
      : value === "spam" || value === "duplicate"
        ? "border-red-900 text-red-400"
        : "border-zinc-700 text-zinc-300";
  return (
    <span className={`rounded border px-1.5 py-0.5 font-mono text-[11px] ${tone}`}>
      {value}
    </span>
  );
}

function LeadDrawer({
  clientId,
  leadId,
  onClose,
  onChanged,
}: {
  clientId: string;
  leadId: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [lead, setLead] = useState<LeadDetail | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[] | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    api<LeadDetail>(`/clients/${clientId}/leads/${leadId}`).then(setLead).catch(() => {});
    api<ConversationMessage[]>(`/clients/${clientId}/leads/${leadId}/conversation`)
      .then(setMessages)
      .catch(() => setMessages([]));
  }, [clientId, leadId]);

  useEffect(reload, [reload]);

  async function act(path: string, body?: unknown) {
    setBusy(true);
    setActionMsg(null);
    try {
      await api(`/clients/${clientId}/leads/${leadId}/${path}`, { method: "POST", body });
      setActionMsg("Done.");
      reload();
      onChanged();
    } catch (e) {
      setActionMsg(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-10 flex justify-end bg-black/60" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="h-full w-full max-w-xl overflow-y-auto border-l border-zinc-800 bg-zinc-950 p-5"
      >
        {!lead ? (
          <p className="font-mono text-sm text-zinc-500">loading…</p>
        ) : (
          <div className="space-y-5">
            <div className="flex items-start gap-3">
              <div>
                <h3 className="text-lg font-semibold text-zinc-100">
                  {lead.contact_name ?? "Unknown caller"}
                </h3>
                <p className="font-mono text-xs text-zinc-500">
                  {lead.phone} · {lead.source_system} · {fmtDate(lead.created_at)}
                </p>
              </div>
              <button
                onClick={onClose}
                className="ml-auto rounded border border-zinc-800 px-2 py-1 font-mono text-xs text-zinc-400 hover:text-zinc-200"
              >
                close
              </button>
            </div>

            <div className="grid grid-cols-2 gap-2 rounded-lg border border-zinc-800 bg-zinc-900/40 p-3 font-mono text-xs">
              <Meta k="classification" v={lead.classification} />
              <Meta k="status" v={lead.qualification_status} />
              <Meta k="intent" v={lead.intent?.intent ?? "—"} />
              <Meta k="score" v={lead.qualification_score?.toString() ?? "—"} />
              <Meta k="service" v={lead.service_type ?? "—"} />
              <Meta k="budget" v={lead.budget_range ?? "—"} />
              <Meta k="sqft" v={lead.sqft?.toString() ?? "—"} />
              <Meta k="timeframe" v={lead.timeframe ?? "—"} />
              <Meta k="outcome" v={`${lead.outcome} ${money(lead.recovered_value)}`} />
              <Meta k="source" v={lead.outcome_source ?? "—"} />
              <Meta k="CRM id" v={lead.external_id ?? "not pushed"} />
              <Meta k="test lead" v={lead.is_test ? "yes" : "no"} />
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                disabled={busy}
                onClick={() => act("repush")}
                className="rounded bg-signal px-3 py-1.5 text-xs font-semibold text-zinc-950 disabled:opacity-40"
              >
                {lead.external_id ? "Re-sync to CRM" : "Push to CRM"}
              </button>
              <button
                disabled={busy}
                onClick={() => act("mark-test", { is_test: !lead.is_test })}
                className="rounded border border-zinc-700 px-3 py-1.5 font-mono text-xs text-zinc-300 hover:border-zinc-500 disabled:opacity-40"
              >
                {lead.is_test ? "Unmark test" : "Mark as test"}
              </button>
              <OutcomeForm
                disabled={busy}
                onSubmit={(outcome, value) =>
                  act("outcome", {
                    outcome,
                    recovered_value: value === "" ? null : value,
                  })
                }
              />
              {actionMsg && (
                <span className="font-mono text-xs text-zinc-400">{actionMsg}</span>
              )}
            </div>

            <div>
              <h4 className="mb-2 font-mono text-xs uppercase tracking-[0.15em] text-signal">
                Conversation
              </h4>
              {!messages ? (
                <p className="font-mono text-xs text-zinc-500">loading…</p>
              ) : messages.length === 0 ? (
                <p className="text-sm text-zinc-500">No messages.</p>
              ) : (
                <div className="space-y-2">
                  {messages.map((m) => (
                    <div
                      key={m.id}
                      className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                        m.direction === "outbound"
                          ? "ml-auto bg-zinc-800 text-zinc-200"
                          : "bg-zinc-900 text-zinc-300"
                      }`}
                    >
                      <p className="whitespace-pre-wrap">{m.body}</p>
                      <p className="mt-1 font-mono text-[10px] text-zinc-500">
                        {m.direction}
                        {m.ai_generated ? " · AI" : ""} · {fmtDate(m.created_at)}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {lead.notes && (
              <div>
                <h4 className="mb-1 font-mono text-xs uppercase tracking-[0.15em] text-signal">
                  Notes
                </h4>
                <p className="whitespace-pre-wrap text-sm text-zinc-300">{lead.notes}</p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Meta({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <span className="text-zinc-600">{k}: </span>
      <span className="text-zinc-300">{v}</span>
    </div>
  );
}

function OutcomeForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (outcome: string, value: string) => void;
}) {
  const [outcome, setOutcome] = useState("won");
  const [value, setValue] = useState("");
  return (
    <span className="flex items-center gap-1.5">
      <select
        value={outcome}
        onChange={(e) => setOutcome(e.target.value)}
        className="rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 font-mono text-xs outline-none"
      >
        <option value="won">won</option>
        <option value="lost">lost</option>
        <option value="open">open</option>
      </select>
      <input
        type="number"
        placeholder="$ booked"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="w-24 rounded border border-zinc-800 bg-zinc-900 px-2 py-1.5 font-mono text-xs outline-none"
      />
      <button
        disabled={disabled || (outcome === "won" && value === "")}
        onClick={() => onSubmit(outcome, value)}
        className="rounded border border-zinc-700 px-2 py-1.5 font-mono text-xs text-zinc-300 hover:border-zinc-500 disabled:opacity-40"
      >
        record outcome
      </button>
    </span>
  );
}
