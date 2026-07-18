import { useCallback, useEffect, useState } from "react";
import {
  api,
  isDemo,
  type ClassificationConfig,
  type ClientConfig,
  type ContactConfig,
  type ConversationConfig,
  type QualificationSchema,
} from "../api";
import { CLIENT_STATUS_LABELS, CLIENT_TIER_LABELS, SPAM_RISK_LABELS, labelFor } from "../labels";
import QualificationEditor from "./QualificationEditor";

/** Editable subset — mirrors ClientConfigUpdate. Only drafted keys are sent. */
type Draft = Partial<{
  timezone: string;
  twilio_number: string | null;
  greeting_template: string | null;
  qualification_prompt: string | null;
  existing_customer_template: string | null;
  vendor_ack_template: string | null;
  vip_keywords: string[];
  vip_value_threshold: number | null;
  service_area_zips: string[];
  ai_interaction_cap_monthly: number;
  notification_emails: string[];
  owner_alert_emails: string[];
  owner_alert_phones: string[];
  classification_config: ClassificationConfig;
  conversation_config: ConversationConfig;
  contact_config: ContactConfig;
  qualification_schema: QualificationSchema;
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
    return <p role="alert" className="text-sm text-danger">{error}</p>;
  }
  if (!config) {
    return <p className="font-mono text-sm text-zinc-400">Loading config…</p>;
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

  const conv: ConversationConfig = draft.conversation_config ?? config.conversation_config;
  const setConv = (patch: Partial<ConversationConfig>) =>
    set("conversation_config", { ...conv, ...patch });

  const contact: ContactConfig = draft.contact_config ?? config.contact_config;
  const setContact = (patch: Partial<ContactConfig>) =>
    set("contact_config", { ...contact, ...patch });

  const schema: QualificationSchema = draft.qualification_schema ?? config.qualification_schema;

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
    <div className="max-w-5xl space-y-6">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold text-zinc-100">{config.business_name}</h2>
        <span className="font-mono text-xs text-zinc-400">
          {labelFor(CLIENT_TIER_LABELS, config.tier)} · {labelFor(CLIENT_STATUS_LABELS, config.status)} · CRM: {config.crm_provider ?? "none"}
          {config.has_crm_credentials ? " (creds ✓)" : " (no creds)"} · webhooks:{" "}
          {config.webhook_integrations.join(", ") || "none"}
        </span>
        <div className="ml-auto flex items-center gap-3">
          {status && <span role="status" className="font-mono text-xs text-success">{status}</span>}
          {error && <span role="alert" className="font-mono text-xs text-danger">{error}</span>}
          <button
            onClick={save}
            disabled={!dirty || saving}
            className="rounded bg-signal px-4 py-2.5 text-sm font-semibold text-zinc-950 disabled:opacity-40"
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
            className="w-full rounded border border-border bg-surface px-3 py-2.5 font-mono text-sm outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
          />
        </Field>
        <Field label="Qualifier prompt override" hint="DEPRECATED — superseded by the qualification schema below; unread">
          <textarea
            rows={3}
            value={(val("qualification_prompt") as string | null) ?? ""}
            onChange={(e) => set("qualification_prompt", e.target.value || null)}
            className="w-full rounded border border-border bg-surface px-3 py-2.5 font-mono text-sm outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Existing-customer ack" hint="service ack for a known customer at voicemail; blank = default. Use {business_name}">
            <textarea
              rows={2}
              value={(val("existing_customer_template") as string | null) ?? ""}
              onChange={(e) => set("existing_customer_template", e.target.value || null)}
              className="w-full rounded border border-border bg-surface px-3 py-2.5 font-mono text-sm outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
            />
          </Field>
          <Field label="Vendor ack" hint="minimal ack for an allowlisted vendor; blank = default. Use {business_name}">
            <textarea
              rows={2}
              value={(val("vendor_ack_template") as string | null) ?? ""}
              onChange={(e) => set("vendor_ack_template", e.target.value || null)}
              className="w-full rounded border border-border bg-surface px-3 py-2.5 font-mono text-sm outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
            />
          </Field>
        </div>
      </Section>

      <Section title="Returning callers">
        <div className="grid gap-2 sm:grid-cols-2">
          <Toggle
            label="Recognize returning callers"
            hint="greet a known caller by name and ask same-project-or-new"
            checked={conv.recognize_returning_callers}
            onChange={(v) => setConv({ recognize_returning_callers: v })}
          />
          <Toggle
            label="Reuse lead on resume"
            hint="a stale-open conversation reuses its lead — no duplicate CRM record"
            checked={conv.reuse_lead_on_resume}
            onChange={(v) => setConv({ reuse_lead_on_resume: v })}
          />
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label="Resume window (hours)" hint="an open lead older than this resumes instead of counting as active">
            <Input
              type="number"
              value={String(conv.resume_window_hours)}
              onChange={(v) => setConv({ resume_window_hours: Number(v) })}
            />
          </Field>
          <Field label="Reopen window (days)" hint="a terminal lead newer than this lets a returning caller reopen with context">
            <Input
              type="number"
              value={String(conv.reopen_window_days)}
              onChange={(v) => setConv({ reopen_window_days: Number(v) })}
            />
          </Field>
        </div>
      </Section>

      <Section title="Contact source of truth">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Source of truth" hint="auto → CRM when a working adapter exists, else TraceFlow">
            <select
              value={contact.source_of_truth}
              onChange={(e) => setContact({ source_of_truth: e.target.value as ContactConfig["source_of_truth"] })}
              className="w-full rounded border border-border bg-surface px-3 py-2.5 text-sm outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
            >
              <option value="auto">auto</option>
              <option value="crm">crm</option>
              <option value="traceflow">traceflow</option>
            </select>
          </Field>
          <Field label="Contact-type cache (days)" hint="a fresh CRM-typed caller skips the CRM + spam lookups">
            <Input
              type="number"
              value={String(contact.contact_type_cache_days)}
              onChange={(v) => setContact({ contact_type_cache_days: Number(v) })}
            />
          </Field>
          <Toggle
            label="CRM write-back"
            hint="push manual type changes back to the CRM (off by default; manual-only)"
            checked={contact.crm_write_back_contact_type}
            onChange={(v) => setContact({ crm_write_back_contact_type: v })}
          />
        </div>
      </Section>

      <Section title="Qualification schema">
        <QualificationEditor
          schema={schema}
          readOnly={isDemo}
          onChange={(s) => set("qualification_schema", s)}
        />
      </Section>

      <Section title="Caller classification">
        <div className="grid gap-2 sm:grid-cols-2">
          {TOGGLES.map(({ key, label, hint }) => (
            <label
              key={key}
              className="flex cursor-pointer items-start gap-2 rounded border border-border bg-surface/60 px-3 py-2"
            >
              <input
                type="checkbox"
                checked={cls[key] as boolean}
                onChange={(e) => setCls({ [key]: e.target.checked })}
                className="mt-0.5 accent-signal"
              />
              <span>
                <span className="block text-sm text-zinc-200">{label}</span>
                <span className="block text-xs text-zinc-400">{hint}</span>
              </span>
            </label>
          ))}
          <label className="flex items-start gap-2 rounded border border-border bg-surface/60 px-3 py-2">
            <span className="grow">
              <span className="block text-sm text-zinc-200">Spam risk threshold</span>
              <span className="block text-xs text-zinc-400">
                stricter = more callers treated as spam
              </span>
            </span>
            <select
              value={cls.spam_risk_threshold}
              onChange={(e) =>
                setCls({ spam_risk_threshold: e.target.value as ClassificationConfig["spam_risk_threshold"] })
              }
              className="rounded border border-border bg-surface px-3 py-2.5 text-sm outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
            >
              <option value="low">{SPAM_RISK_LABELS.low}</option>
              <option value="moderate">{SPAM_RISK_LABELS.moderate}</option>
              <option value="high">{SPAM_RISK_LABELS.high}</option>
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
    <section className="rounded-lg border border-border bg-surface/40 p-4">
      <h3 className="mb-3 font-mono text-sm uppercase tracking-[0.15em] text-signal">
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
      <span className="font-mono text-sm uppercase tracking-wider text-zinc-400">
        {label}
      </span>
      {hint && <span className="block text-xs text-zinc-400">{hint}</span>}
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
      className="w-full rounded border border-border bg-surface px-3 py-2.5 text-sm outline-none focus:border-signal focus-visible:ring-2 focus-visible:ring-signal/70"
    />
  );
}

function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 rounded border border-border bg-surface/60 px-3 py-2">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 accent-signal"
      />
      <span>
        <span className="block text-sm text-zinc-200">{label}</span>
        <span className="block text-xs text-zinc-400">{hint}</span>
      </span>
    </label>
  );
}
