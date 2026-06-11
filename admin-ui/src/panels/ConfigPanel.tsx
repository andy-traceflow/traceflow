import { useCallback, useEffect, useState } from "react";
import { api, type ClassificationConfig, type ClientConfig } from "../api";

/** Editable subset — mirrors ClientConfigUpdate. Only drafted keys are sent. */
type Draft = Partial<{
  timezone: string;
  twilio_number: string | null;
  greeting_template: string | null;
  qualification_prompt: string | null;
  vip_keywords: string[];
  vip_value_threshold: number | null;
  service_area_zips: string[];
  ai_interaction_cap_monthly: number;
  notification_emails: string[];
  owner_alert_emails: string[];
  owner_alert_phones: string[];
  classification_config: ClassificationConfig;
  existing_customer_alert_contact: string | null;
  vendor_allowlist: string[];
}>;

const csv = (xs: string[]) => xs.join(", ");
const parseCsv = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

const TOGGLES: { key: keyof ClassificationConfig; label: string; hint: string }[] = [
  { key: "crm_lookup_enabled", label: "CRM lookup", hint: "identify existing customers/vendors before texting" },
  { key: "spam_filtering_enabled", label: "Spam filtering", hint: "score callers before the greeting" },
  { key: "text_existing_customers", label: "Text existing customers", hint: "send the greeting to known customers" },
  { key: "text_vendors", label: "Text vendors", hint: "send the greeting to allowlisted vendors" },
  { key: "drop_spam_silently", label: "Drop spam silently", hint: "no SMS, no owner alert for spam" },
];

export default function ConfigPanel({ clientId }: { clientId: string }) {
  const [config, setConfig] = useState<ClientConfig | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    setConfig(null);
    setDraft({});
    setError(null);
    setStatus(null);
    api<ClientConfig>(`/clients/${clientId}/config`)
      .then(setConfig)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load config"));
  }, [clientId]);

  useEffect(load, [load]);

  if (error && !config) {
    return <p className="text-sm text-red-400">{error}</p>;
  }
  if (!config) {
    return <p className="font-mono text-sm text-zinc-500">loading config…</p>;
  }

  // current value = draft override or loaded config
  const val = <K extends keyof Draft>(key: K): NonNullable<Draft[K]> | ClientConfig[K & keyof ClientConfig] =>
    (key in draft ? draft[key] : config[key as keyof ClientConfig]) as never;
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  const cls: ClassificationConfig = (draft.classification_config ??
    config.classification_config) as ClassificationConfig;
  const setCls = (patch: Partial<ClassificationConfig>) =>
    set("classification_config", { ...cls, ...patch });

  const dirty = Object.keys(draft).length > 0;

  async function save() {
    setSaving(true);
    setError(null);
    setStatus(null);
    try {
      const updated = await api<ClientConfig>(`/clients/${clientId}/config`, {
        method: "PUT",
        body: draft,
      });
      setConfig(updated);
      setDraft({});
      setStatus(`Saved ${new Date().toLocaleTimeString()}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold text-zinc-100">{config.business_name}</h2>
        <span className="font-mono text-xs text-zinc-500">
          {config.tier} · {config.status} · CRM: {config.crm_provider ?? "none"}
          {config.has_crm_credentials ? " (creds ✓)" : " (no creds)"} · webhooks:{" "}
          {config.webhook_integrations.join(", ") || "none"}
        </span>
        <div className="ml-auto flex items-center gap-3">
          {status && <span className="font-mono text-xs text-emerald-400">{status}</span>}
          {error && <span className="font-mono text-xs text-red-400">{error}</span>}
          <button
            onClick={save}
            disabled={!dirty || saving}
            className="rounded bg-signal px-4 py-1.5 text-sm font-semibold text-zinc-950 disabled:opacity-40"
          >
            {saving ? "Saving…" : dirty ? "Save changes" : "No changes"}
          </button>
        </div>
      </div>

      <Section title="Messaging">
        <Field label="Greeting template" hint="missed-call SMS; blank = platform default">
          <textarea
            rows={3}
            value={(val("greeting_template") as string | null) ?? ""}
            onChange={(e) => set("greeting_template", e.target.value || null)}
            className="w-full rounded border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm outline-none focus:border-signal"
          />
        </Field>
        <Field label="Qualifier prompt override" hint="blank = platform default prompt">
          <textarea
            rows={5}
            value={(val("qualification_prompt") as string | null) ?? ""}
            onChange={(e) => set("qualification_prompt", e.target.value || null)}
            className="w-full rounded border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm outline-none focus:border-signal"
          />
        </Field>
      </Section>

      <Section title="Caller classification (lifecycle v2)">
        <div className="grid gap-2 sm:grid-cols-2">
          {TOGGLES.map(({ key, label, hint }) => (
            <label
              key={key}
              className="flex cursor-pointer items-start gap-2 rounded border border-zinc-800 bg-zinc-900/60 px-3 py-2"
            >
              <input
                type="checkbox"
                checked={cls[key] as boolean}
                onChange={(e) => setCls({ [key]: e.target.checked })}
                className="mt-0.5 accent-[#ff6a00]"
              />
              <span>
                <span className="block text-sm text-zinc-200">{label}</span>
                <span className="block text-xs text-zinc-500">{hint}</span>
              </span>
            </label>
          ))}
          <label className="flex items-start gap-2 rounded border border-zinc-800 bg-zinc-900/60 px-3 py-2">
            <span className="grow">
              <span className="block text-sm text-zinc-200">Spam risk threshold</span>
              <span className="block text-xs text-zinc-500">
                stricter = more callers treated as spam
              </span>
            </span>
            <select
              value={cls.spam_risk_threshold}
              onChange={(e) =>
                setCls({ spam_risk_threshold: e.target.value as ClassificationConfig["spam_risk_threshold"] })
              }
              className="rounded border border-zinc-800 bg-zinc-900 px-2 py-1 text-sm outline-none focus:border-signal"
            >
              <option value="low">low</option>
              <option value="moderate">moderate</option>
              <option value="high">high</option>
            </select>
          </label>
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label="Existing-customer alert contact" hint="who gets pinged when a customer hits voicemail">
            <Input
              value={(val("existing_customer_alert_contact") as string | null) ?? ""}
              onChange={(v) => set("existing_customer_alert_contact", v || null)}
            />
          </Field>
          <Field label="Vendor allowlist (comma-separated phones)" hint="never treated as leads">
            <Input
              value={csv(val("vendor_allowlist") as string[])}
              onChange={(v) => set("vendor_allowlist", parseCsv(v))}
            />
          </Field>
        </div>
      </Section>

      <Section title="VIP alerts">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="VIP keywords (comma-separated)">
            <Input
              value={csv(val("vip_keywords") as string[])}
              onChange={(v) => set("vip_keywords", parseCsv(v))}
            />
          </Field>
          <Field label="VIP value threshold ($)">
            <Input
              type="number"
              value={String((val("vip_value_threshold") as number | null) ?? "")}
              onChange={(v) => set("vip_value_threshold", v === "" ? null : Number(v))}
            />
          </Field>
        </div>
      </Section>

      <Section title="Notifications">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Owner alert emails">
            <Input
              value={csv(val("owner_alert_emails") as string[])}
              onChange={(v) => set("owner_alert_emails", parseCsv(v))}
            />
          </Field>
          <Field label="Ops notification emails">
            <Input
              value={csv(val("notification_emails") as string[])}
              onChange={(v) => set("notification_emails", parseCsv(v))}
            />
          </Field>
          <Field label="Owner alert phones">
            <Input
              value={csv(val("owner_alert_phones") as string[])}
              onChange={(v) => set("owner_alert_phones", parseCsv(v))}
            />
          </Field>
        </div>
      </Section>

      <Section title="Operations">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Timezone (IANA)" hint="drives digest + monthly report timing">
            <Input value={val("timezone") as string} onChange={(v) => set("timezone", v)} />
          </Field>
          <Field label="Twilio number (E.164)">
            <Input
              value={(val("twilio_number") as string | null) ?? ""}
              onChange={(v) => set("twilio_number", v || null)}
            />
          </Field>
          <Field label="Service-area ZIPs">
            <Input
              value={csv(val("service_area_zips") as string[])}
              onChange={(v) => set("service_area_zips", parseCsv(v))}
            />
          </Field>
          <Field label="AI interaction cap / month">
            <Input
              type="number"
              value={String(val("ai_interaction_cap_monthly") as number)}
              onChange={(v) => set("ai_interaction_cap_monthly", Number(v))}
            />
          </Field>
        </div>
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
      <h3 className="mb-3 font-mono text-xs uppercase tracking-[0.15em] text-signal">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="font-mono text-xs uppercase tracking-wider text-zinc-500">
        {label}
      </span>
      {hint && <span className="block text-xs text-zinc-600">{hint}</span>}
      <div className="mt-1">{children}</div>
    </label>
  );
}

function Input({
  value,
  onChange,
  type = "text",
}: {
  value: string;
  onChange: (v: string) => void;
  type?: string;
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-signal"
    />
  );
}
