import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type FieldMapping } from "../api";
import { FIELD_TYPE_LABELS, labelFor } from "../labels";

const FIELD_TYPES = ["standard", "custom_field", "custom_property", "column"] as const;
const EMPTY: FieldMapping = {
  integration: "crm",
  canonical_field: "",
  external_field: "",
  external_field_type: "standard",
  transform: null,
  notes: null,
};

export default function MappingsPanel({ clientId }: { clientId: string }) {
  const [mappings, setMappings] = useState<FieldMapping[] | null>(null);
  const [draft, setDraft] = useState<FieldMapping>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError(null);
    api<FieldMapping[]>(`/clients/${clientId}/field-mappings`)
      .then(setMappings)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load mappings"));
  }, [clientId]);

  useEffect(() => {
    setDraft(EMPTY);
    load();
  }, [load]);

  async function upsert(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`/clients/${clientId}/field-mappings`, { method: "PUT", body: draft });
      setDraft(EMPTY);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function remove(m: FieldMapping) {
    if (!confirm(`Delete mapping ${m.integration}:${m.canonical_field}?`)) return;
    setError(null);
    try {
      await api(`/clients/${clientId}/field-mappings/${m.integration}/${m.canonical_field}`, {
        method: "DELETE",
      });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold text-zinc-100">Field mappings</h2>
        <span className="font-mono text-xs text-zinc-400">
          canonical lead fields → the client's CRM/integration fields; edits apply on the
          next push
        </span>
      </div>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}

      {!mappings ? (
        <p className="font-mono text-sm text-zinc-400">Loading…</p>
      ) : mappings.length === 0 ? (
        <p className="text-sm text-zinc-400">
          No mappings — zero-config defaults apply where the adapter has them (HubSpot
          standard properties); add explicit rows for custom fields.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface font-mono text-sm uppercase tracking-wider text-zinc-400">
              <tr>
                <th className="px-3 py-2">Integration</th>
                <th className="px-3 py-2">Canonical</th>
                <th className="px-3 py-2">External field</th>
                <th className="px-3 py-2">Type</th>
                <th className="px-3 py-2">Notes</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {mappings.map((m) => (
                <tr
                  key={`${m.integration}:${m.canonical_field}`}
                  className="border-t border-border/70"
                >
                  <td className="px-3 py-2 font-mono text-xs">{m.integration}</td>
                  <td className="px-3 py-2 font-mono text-xs text-zinc-200">
                    {m.canonical_field}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{m.external_field}</td>
                  <td className="px-3 py-2 font-mono text-xs text-zinc-400">
                    {labelFor(FIELD_TYPE_LABELS, m.external_field_type)}
                  </td>
                  <td className="px-3 py-2 text-xs text-zinc-400">{m.notes ?? ""}</td>
                  <td className="px-3 py-2">
                    <div className="flex justify-end gap-1">
                      <button
                        onClick={() => setDraft(m)}
                        className="rounded px-3 py-1.5 font-mono text-sm text-zinc-400 hover:bg-surface hover:text-zinc-100"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => remove(m)}
                        className="rounded px-3 py-1.5 font-mono text-sm text-danger hover:bg-danger/10"
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <form
        onSubmit={upsert}
        className="grid max-w-4xl grid-cols-1 items-end gap-2 rounded-lg border border-border bg-surface/40 p-3 sm:grid-cols-2 lg:grid-cols-5"
      >
        <L label="integration">
          <input
            required
            value={draft.integration}
            onChange={(e) => setDraft({ ...draft, integration: e.target.value })}
            className="w-full rounded border border-border bg-surface px-3 py-2.5 font-mono text-sm outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
          />
        </L>
        <L label="canonical field">
          <input
            required
            placeholder="sqft"
            value={draft.canonical_field}
            onChange={(e) => setDraft({ ...draft, canonical_field: e.target.value })}
            className="w-full rounded border border-border bg-surface px-3 py-2.5 font-mono text-sm outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
          />
        </L>
        <L label="external field">
          <input
            required
            placeholder="cf_abc123"
            value={draft.external_field}
            onChange={(e) => setDraft({ ...draft, external_field: e.target.value })}
            className="w-full rounded border border-border bg-surface px-3 py-2.5 font-mono text-sm outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
          />
        </L>
        <L label="type">
          <select
            value={draft.external_field_type}
            onChange={(e) =>
              setDraft({
                ...draft,
                external_field_type: e.target.value as FieldMapping["external_field_type"],
              })
            }
            className="w-full rounded border border-border bg-surface px-3 py-2.5 font-mono text-sm outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
          >
            {FIELD_TYPES.map((t) => (
              <option key={t} value={t}>
                {labelFor(FIELD_TYPE_LABELS, t)}
              </option>
            ))}
          </select>
        </L>
        <button
          type="submit"
          disabled={busy}
          className="rounded bg-signal px-3 py-2.5 text-sm font-semibold text-zinc-950 disabled:opacity-40"
        >
          Save mapping
        </button>
      </form>
    </div>
  );
}

function L({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="font-mono text-sm uppercase tracking-wider text-zinc-400">
        {label}
      </span>
      <div className="mt-1">{children}</div>
    </label>
  );
}
