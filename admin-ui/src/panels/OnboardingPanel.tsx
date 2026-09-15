import { useCallback, useEffect, useState } from "react";
import {
  api,
  isDemo,
  type OnboardingDetail,
  type OnboardingListItem,
  type PromoteResult,
} from "../api";

const STATUS_BADGE: Record<OnboardingListItem["status"], string> = {
  new: "border-signal/40 bg-signal/10 text-signal",
  reviewed: "border-border bg-surface text-zinc-300",
  promoted: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400",
  rejected: "border-danger/40 bg-danger/10 text-danger",
};

/**
 * Admin-side review + promote for onboarding submissions. NOT client-scoped —
 * submissions are pre-tenant (migration 022). Promoting one creates the live
 * client via /api/admin/onboarding-submissions/{id}/promote, then bubbles up so
 * the Shell can refresh the client switcher.
 */
export default function OnboardingPanel({ onPromoted }: { onPromoted: () => void }) {
  const [list, setList] = useState<OnboardingListItem[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<OnboardingDetail | null>(null);
  const [mappedText, setMappedText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadList = useCallback(() => {
    setError(null);
    api<OnboardingListItem[]>("/onboarding-submissions")
      .then(setList)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load submissions"));
  }, []);

  useEffect(() => {
    loadList();
  }, [loadList]);

  const openDetail = useCallback((id: string) => {
    setSelectedId(id);
    setDetail(null);
    setError(null);
    setNotice(null);
    api<OnboardingDetail>(`/onboarding-submissions/${id}`)
      .then((d) => {
        setDetail(d);
        setMappedText(JSON.stringify(d.mapped_config, null, 2));
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load submission"));
  }, []);

  function parseMapped(): Record<string, unknown> | null {
    try {
      const parsed = JSON.parse(mappedText);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setError("Mapped config must be a JSON object");
        return null;
      }
      return parsed as Record<string, unknown>;
    } catch {
      setError("Mapped config is not valid JSON");
      return null;
    }
  }

  async function saveDraft() {
    if (!detail) return;
    const mapped = parseMapped();
    if (!mapped) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api<OnboardingDetail>(`/onboarding-submissions/${detail.id}`, {
        method: "PUT",
        body: { mapped_config: mapped, status: "reviewed" },
      });
      setDetail(updated);
      setMappedText(JSON.stringify(updated.mapped_config, null, 2));
      setNotice("Draft saved.");
      loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function promote() {
    if (!detail) return;
    // Persist any edits first so what you reviewed is what gets provisioned.
    const mapped = parseMapped();
    if (!mapped) return;
    if (!confirm(`Promote "${detail.business_name ?? "this submission"}" into a live client?`)) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/onboarding-submissions/${detail.id}`, {
        method: "PUT",
        body: { mapped_config: mapped },
      });
      const result = await api<PromoteResult>(
        `/onboarding-submissions/${detail.id}/promote`,
        { method: "POST" },
      );
      setNotice(`Promoted — new client ${result.client_id}. It's now in the client switcher.`);
      onPromoted();
      loadList();
      openDetail(detail.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Promote failed");
    } finally {
      setBusy(false);
    }
  }

  async function reject() {
    if (!detail) return;
    if (!confirm("Reject this submission? It stays on record but won't be promoted.")) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api<OnboardingDetail>(`/onboarding-submissions/${detail.id}`, {
        method: "PUT",
        body: { status: "rejected" },
      });
      setDetail(updated);
      setNotice("Submission rejected.");
      loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reject failed");
    } finally {
      setBusy(false);
    }
  }

  const alreadyPromoted = detail?.status === "promoted";

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold text-zinc-100">Onboarding</h2>
        <span className="font-mono text-xs text-zinc-400">
          review incoming submissions, then promote one into a live client
        </span>
      </div>

      {error && (
        <p role="alert" className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-400">
          {notice}
        </p>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,22rem)_1fr]">
        {/* List */}
        <div className="overflow-hidden rounded-lg border border-border">
          {!list ? (
            <p className="p-3 font-mono text-sm text-zinc-400">Loading…</p>
          ) : list.length === 0 ? (
            <p className="p-3 text-sm text-zinc-400">
              No submissions yet. They'll appear here once the onboarding form starts posting.
            </p>
          ) : (
            <ul className="divide-y divide-border/70">
              {list.map((s) => (
                <li key={s.id}>
                  <button
                    onClick={() => openDetail(s.id)}
                    aria-current={selectedId === s.id}
                    className={`flex w-full flex-col gap-1 px-3 py-2.5 text-left hover:bg-surface ${
                      selectedId === s.id ? "bg-surface" : ""
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm text-zinc-100">
                        {s.business_name ?? "(no business name)"}
                      </span>
                      <span
                        className={`ml-auto shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${STATUS_BADGE[s.status]}`}
                      >
                        {s.status}
                      </span>
                    </div>
                    <span className="font-mono text-[11px] text-zinc-500">
                      {s.source} · {new Date(s.created_at).toLocaleDateString()}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Detail */}
        <div className="rounded-lg border border-border p-4">
          {!detail ? (
            <p className="font-mono text-sm text-zinc-400">
              {selectedId ? "Loading…" : "Select a submission to review."}
            </p>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-base font-semibold text-zinc-100">
                  {detail.business_name ?? "(no business name)"}
                </h3>
                <span
                  className={`rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${STATUS_BADGE[detail.status]}`}
                >
                  {detail.status}
                </span>
                {detail.promoted_client_id && (
                  <span className="font-mono text-[11px] text-zinc-500">
                    client {detail.promoted_client_id}
                  </span>
                )}
              </div>

              <div>
                <label className="mb-1 block font-mono text-sm uppercase tracking-wider text-zinc-400">
                  Mapped config (editable) — this is what promote provisions
                </label>
                <textarea
                  value={mappedText}
                  onChange={(e) => setMappedText(e.target.value)}
                  spellCheck={false}
                  disabled={alreadyPromoted || isDemo}
                  rows={16}
                  className="w-full rounded border border-border bg-surface px-3 py-2 font-mono text-xs leading-relaxed text-zinc-200 outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70 disabled:opacity-60"
                />
              </div>

              <details className="rounded border border-border/70 bg-surface/40">
                <summary className="cursor-pointer px-3 py-2 font-mono text-xs uppercase tracking-wider text-zinc-400">
                  Raw submission
                </summary>
                <pre className="max-h-72 overflow-auto px-3 py-2 font-mono text-[11px] leading-relaxed text-zinc-400">
                  {JSON.stringify(detail.raw_payload, null, 2)}
                </pre>
              </details>

              {!isDemo && (
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={promote}
                    disabled={busy || alreadyPromoted || detail.status === "rejected"}
                    className="rounded bg-signal px-3 py-2.5 text-sm font-semibold text-zinc-950 disabled:opacity-40"
                  >
                    {alreadyPromoted ? "Promoted" : "Promote to client"}
                  </button>
                  <button
                    onClick={saveDraft}
                    disabled={busy || alreadyPromoted}
                    className="rounded border border-border px-3 py-2.5 font-mono text-sm text-zinc-300 hover:border-zinc-600 hover:text-zinc-100 disabled:opacity-40"
                  >
                    Save draft
                  </button>
                  <button
                    onClick={reject}
                    disabled={busy || alreadyPromoted || detail.status === "rejected"}
                    className="rounded border border-danger/40 px-3 py-2.5 font-mono text-sm text-danger hover:bg-danger/10 disabled:opacity-40"
                  >
                    Reject
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
