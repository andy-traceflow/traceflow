import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type ConversationMessage,
  type LeadDetail,
  type LeadItem,
  type LeadList,
} from "../api";
import {
  CLASSIFICATION_LABELS,
  CLASSIFICATION_SINGULAR_LABELS,
  INTENT_LABELS,
  OUTCOME_LABELS,
  OUTCOME_SOURCE_LABELS,
  QUALIFICATION_STATUS_LABELS,
  labelFor,
} from "../labels";

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
          aria-label="Filter leads by classification"
          value={classification}
          onChange={(e) => setClassification(e.target.value)}
          className="rounded border border-border bg-surface px-2 py-1.5 font-mono text-xs outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
        >
          {CLASSIFICATIONS.map((c) => (
            <option key={c} value={c}>
              {labelFor(CLASSIFICATION_LABELS, c)}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 font-mono text-xs text-zinc-400">
          <input
            type="checkbox"
            checked={includeTest}
            onChange={(e) => setIncludeTest(e.target.checked)}
            className="accent-signal"
          />
          include test leads
        </label>
        {leads && (
          <span className="ml-auto font-mono text-xs text-zinc-400">
            {leads.count} total
          </span>
        )}
      </div>

      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      {!leads && !error && <p className="font-mono text-sm text-zinc-400">loading…</p>}

      {leads && leads.data.length === 0 && (
        <p className="py-8 text-center text-sm text-zinc-400">
          {classification === "all"
            ? "No leads yet."
            : `No ${labelFor(CLASSIFICATION_LABELS, classification).toLowerCase()} yet.`}
        </p>
      )}

      {leads && leads.data.length > 0 && (
        <>
          {/* Desktop: dense table. Mobile (<sm): stacked cards — adapt, don't shrink. */}
          <div className="hidden overflow-x-auto rounded-lg border border-border sm:block">
            <table className="w-full text-left text-sm">
              <thead className="bg-surface font-mono text-xs uppercase tracking-wider text-zinc-400">
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
          <ul className="space-y-2 sm:hidden">
            {leads.data.map((l) => (
              <LeadCard key={l.id} lead={l} onOpen={() => setSelectedId(l.id)} />
            ))}
          </ul>
        </>
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
      tabIndex={0}
      aria-label={`Open lead: ${lead.contact_name ?? lead.phone ?? "details"}`}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      className="cursor-pointer border-t border-border/70 hover:bg-surface/70 focus-visible:bg-surface/70"
    >
      <td className="px-3 py-2 font-mono text-xs text-zinc-400">{fmtDate(lead.created_at)}</td>
      <td className="px-3 py-2">
        <div className="text-zinc-200">{lead.contact_name ?? "Unknown caller"}</div>
        <div className="font-mono text-xs text-zinc-400">{lead.phone}</div>
      </td>
      <td className="px-3 py-2 text-zinc-300">
        {lead.service_type ?? "—"}
        {lead.budget_range && (
          <span className="ml-1 font-mono text-xs text-zinc-400">({lead.budget_range})</span>
        )}
      </td>
      <td className="px-3 py-2">
        <StatusBadge value={lead.qualification_status} />
        {lead.is_test && (
          <span className="ml-1 rounded bg-surface-raised px-1.5 py-0.5 font-mono text-xs text-zinc-400">
            TEST
          </span>
        )}
      </td>
      <td className="px-3 py-2 font-mono text-xs">
        {lead.outcome === "won" ? (
          <span className="text-success">{labelFor(OUTCOME_LABELS, lead.outcome)} {money(lead.recovered_value)}</span>
        ) : (
          <span className="text-zinc-400">{labelFor(OUTCOME_LABELS, lead.outcome)}</span>
        )}
      </td>
      <td className="px-3 py-2 font-mono text-xs">
        {lead.external_id ? (
          <span className="text-success">pushed</span>
        ) : (
          <span className="text-zinc-400">—</span>
        )}
      </td>
      <td className="px-3 py-2 font-mono text-xs text-zinc-400">{lead.message_count}</td>
    </tr>
  );
}

function LeadCard({ lead, onOpen }: { lead: LeadItem; onOpen: () => void }) {
  return (
    <li
      role="button"
      tabIndex={0}
      aria-label={`Open lead: ${lead.contact_name ?? lead.phone ?? "details"}`}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      className="cursor-pointer rounded-lg border border-border bg-surface/40 p-3 hover:bg-surface/70 focus-visible:bg-surface/70"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-zinc-200">{lead.contact_name ?? "Unknown caller"}</div>
          <div className="font-mono text-xs text-zinc-400">{lead.phone}</div>
        </div>
        <StatusBadge value={lead.qualification_status} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs text-zinc-400">
        <span>{fmtDate(lead.created_at)}</span>
        {lead.service_type && <span>{lead.service_type}</span>}
        {lead.outcome === "won" ? (
          <span className="text-success">{labelFor(OUTCOME_LABELS, lead.outcome)} {money(lead.recovered_value)}</span>
        ) : (
          <span>{labelFor(OUTCOME_LABELS, lead.outcome)}</span>
        )}
        {lead.external_id && <span className="text-success">pushed</span>}
        <span>{lead.message_count} msg</span>
        {lead.is_test && (
          <span className="rounded bg-surface-raised px-1.5 py-0.5 text-zinc-400">TEST</span>
        )}
      </div>
    </li>
  );
}

function StatusBadge({ value }: { value: string }) {
  const tone =
    value === "qualified" || value === "high_value"
      ? "border-success/40 text-success"
      : value === "spam" || value === "duplicate"
        ? "border-danger/40 text-danger"
        : "border-border-strong text-zinc-300";
  return (
    <span className={`rounded border px-1.5 py-0.5 font-mono text-xs ${tone}`}>
      {labelFor(QUALIFICATION_STATUS_LABELS, value)}
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
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  const reload = useCallback(() => {
    api<LeadDetail>(`/clients/${clientId}/leads/${leadId}`).then(setLead).catch(() => {});
    api<ConversationMessage[]>(`/clients/${clientId}/leads/${leadId}/conversation`)
      .then(setMessages)
      .catch(() => setMessages([]));
  }, [clientId, leadId]);

  useEffect(reload, [reload]);

  // Dialog a11y: focus the panel on open, restore focus on close, Esc to close.
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    panelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previouslyFocused?.focus();
    };
  }, []);

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
    <div
      className="fixed inset-0 z-10 flex justify-end bg-black/60 animate-[tf-fade-in_150ms_ease-out]"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Lead details"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="h-full w-full max-w-xl overflow-y-auto border-l border-border bg-zinc-950 p-5 outline-none animate-[tf-slide-in_200ms_ease-out]"
      >
        {!lead ? (
          <p className="font-mono text-sm text-zinc-400">loading…</p>
        ) : (
          <div className="space-y-5">
            <div className="flex items-start gap-3">
              <div>
                <h3 className="text-lg font-semibold text-zinc-100">
                  {lead.contact_name ?? "Unknown caller"}
                </h3>
                <p className="font-mono text-xs text-zinc-400">
                  {lead.phone} · {lead.source_system} · {fmtDate(lead.created_at)}
                </p>
              </div>
              <button
                onClick={onClose}
                className="ml-auto rounded border border-border px-2.5 py-1.5 font-mono text-xs text-zinc-400 hover:text-zinc-200"
              >
                close
              </button>
            </div>

            <div className="grid grid-cols-2 gap-2 rounded-lg border border-border bg-surface/40 p-3 font-mono text-xs">
              <Meta k="classification" v={labelFor(CLASSIFICATION_SINGULAR_LABELS, lead.classification)} />
              <Meta k="status" v={labelFor(QUALIFICATION_STATUS_LABELS, lead.qualification_status)} />
              <Meta k="intent" v={labelFor(INTENT_LABELS, lead.intent?.intent)} />
              <Meta k="score" v={lead.qualification_score?.toString() ?? "—"} />
              <Meta k="service" v={lead.service_type ?? "—"} />
              <Meta k="budget" v={lead.budget_range ?? "—"} />
              <Meta k="sqft" v={lead.sqft?.toString() ?? "—"} />
              <Meta k="timeframe" v={lead.timeframe ?? "—"} />
              <Meta k="outcome" v={`${labelFor(OUTCOME_LABELS, lead.outcome)} ${money(lead.recovered_value)}`} />
              <Meta k="source" v={labelFor(OUTCOME_SOURCE_LABELS, lead.outcome_source)} />
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
                className="rounded border border-border-strong px-3 py-1.5 font-mono text-xs text-zinc-300 hover:border-zinc-500 disabled:opacity-40"
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
                <p className="font-mono text-xs text-zinc-400">loading…</p>
              ) : messages.length === 0 ? (
                <p className="text-sm text-zinc-400">No messages.</p>
              ) : (
                <div className="space-y-2">
                  {messages.map((m) => (
                    <div
                      key={m.id}
                      className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                        m.direction === "outbound"
                          ? "ml-auto bg-surface-raised text-zinc-200"
                          : "bg-surface text-zinc-300"
                      }`}
                    >
                      <p className="whitespace-pre-wrap">{m.body}</p>
                      <p className="mt-1 font-mono text-xs text-zinc-400">
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
      <span className="text-zinc-400">{k}: </span>
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
        aria-label="Lead outcome"
        value={outcome}
        onChange={(e) => setOutcome(e.target.value)}
        className="rounded border border-border bg-surface px-2 py-1.5 font-mono text-xs outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
      >
        <option value="won">{OUTCOME_LABELS.won}</option>
        <option value="lost">{OUTCOME_LABELS.lost}</option>
        <option value="open">{OUTCOME_LABELS.open}</option>
      </select>
      <input
        type="number"
        placeholder="$ booked"
        aria-label="Recovered value in dollars"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="w-24 rounded border border-border bg-surface px-2 py-1.5 font-mono text-xs outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
      />
      <button
        disabled={disabled || (outcome === "won" && value === "")}
        onClick={() => onSubmit(outcome, value)}
        className="rounded border border-border-strong px-2 py-1.5 font-mono text-xs text-zinc-300 hover:border-zinc-500 disabled:opacity-40"
      >
        record outcome
      </button>
    </span>
  );
}
