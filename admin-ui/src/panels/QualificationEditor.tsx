import type { QualField, QualFieldType, QualificationSchema } from "../api";

/** Field-list editor for the client's qualification schema. Edits the common
 *  per-field attributes (label/type/scope/required/weight/maps_to/ask/options)
 *  and the schema knobs; advanced attributes (depends_on / disqualify_if /
 *  hard_gate / unit) are preserved untouched on the field object and surfaced
 *  read-only as tags. */

const LEAD_COLUMNS = ["contact_name", "service_type", "sqft", "budget_range", "timeframe", "address"];
const TYPES: QualFieldType[] = ["string", "number", "enum", "boolean"];

const csv = (xs: string[] | null | undefined) => (xs ?? []).join(", ");
const parseCsv = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

const EMPTY_SCHEMA: QualificationSchema = {
  fields: [],
  min_score_to_qualify: 60,
  max_turns: 8,
  max_questions_per_message: 1,
  ask_budget: false,
};

export default function QualificationEditor({
  schema: raw,
  onChange,
  readOnly,
}: {
  schema: QualificationSchema;
  onChange: (s: QualificationSchema) => void;
  readOnly?: boolean;
}) {
  // A client with no schema stored yet resolves to the default server-side;
  // guard here so the editor still renders (with an empty field list).
  const schema: QualificationSchema =
    raw && Array.isArray(raw.fields) ? raw : { ...EMPTY_SCHEMA, ...raw };

  const setField = (i: number, patch: Partial<QualField>) => {
    const fields = schema.fields.map((f, j) => (j === i ? { ...f, ...patch } : f));
    onChange({ ...schema, fields });
  };
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= schema.fields.length) return;
    const fields = [...schema.fields];
    [fields[i], fields[j]] = [fields[j], fields[i]];
    onChange({ ...schema, fields });
  };
  const remove = (i: number) =>
    onChange({ ...schema, fields: schema.fields.filter((_, j) => j !== i) });
  const add = () =>
    onChange({
      ...schema,
      fields: [
        ...schema.fields,
        {
          key: `field_${schema.fields.length + 1}`,
          label: "New field",
          type: "string",
          scope: "project",
          required: false,
          weight: 0,
          ask: "",
          maps_to: null,
          options: null,
        },
      ],
    });

  return (
    <div className="space-y-4">
      {/* Schema-level knobs */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Num
          label="Min score to qualify"
          value={schema.min_score_to_qualify}
          onChange={(v) => onChange({ ...schema, min_score_to_qualify: v })}
          disabled={readOnly}
        />
        <Num
          label="Max turns"
          value={schema.max_turns}
          onChange={(v) => onChange({ ...schema, max_turns: v })}
          disabled={readOnly}
        />
        <Num
          label="Max questions / message"
          value={schema.max_questions_per_message}
          onChange={(v) => onChange({ ...schema, max_questions_per_message: v })}
          disabled={readOnly}
        />
        <label className="flex items-center gap-2 self-end rounded border border-border bg-surface/60 px-3 py-2.5">
          <input
            type="checkbox"
            checked={schema.ask_budget}
            disabled={readOnly}
            onChange={(e) => onChange({ ...schema, ask_budget: e.target.checked })}
            className="accent-signal"
          />
          <span className="text-sm text-zinc-200">Ask budget directly</span>
        </label>
      </div>

      {/* Field list */}
      <div className="space-y-3">
        {schema.fields.map((f, i) => (
          <div key={i} className="rounded border border-border bg-surface/40 p-3">
            <div className="mb-2 flex items-center gap-2">
              <input
                value={f.key}
                disabled={readOnly}
                onChange={(e) => setField(i, { key: e.target.value })}
                className="w-40 rounded border border-border bg-surface px-2 py-1 font-mono text-xs text-signal outline-none focus:border-signal"
              />
              <span className="flex flex-wrap gap-1">
                {f.hard_gate && <Tag>gate: {f.hard_gate}</Tag>}
                {f.depends_on && <Tag>depends: {Object.keys(f.depends_on).join(",")}</Tag>}
                {f.disqualify_if && <Tag>disqualify_if</Tag>}
                {f.unit && <Tag>{f.unit}</Tag>}
              </span>
              {!readOnly && (
                <span className="ml-auto flex items-center gap-1">
                  <IconBtn label="Move up" onClick={() => move(i, -1)}>↑</IconBtn>
                  <IconBtn label="Move down" onClick={() => move(i, 1)}>↓</IconBtn>
                  <IconBtn label="Remove field" onClick={() => remove(i)} danger>
                    ✕
                  </IconBtn>
                </span>
              )}
            </div>

            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <Text label="Label" value={f.label} disabled={readOnly} onChange={(v) => setField(i, { label: v })} />
              <Select
                label="Type"
                value={f.type}
                disabled={readOnly}
                options={TYPES}
                onChange={(v) => setField(i, { type: v as QualFieldType })}
              />
              <Select
                label="Scope"
                value={f.scope}
                disabled={readOnly}
                options={["person", "project"]}
                onChange={(v) => setField(i, { scope: v as QualField["scope"] })}
              />
              <Select
                label="Maps to (lead column)"
                value={f.maps_to ?? ""}
                disabled={readOnly}
                options={["", ...LEAD_COLUMNS]}
                onChange={(v) => setField(i, { maps_to: v || null })}
              />
              <Num
                label="Weight"
                value={f.weight}
                disabled={readOnly}
                onChange={(v) => setField(i, { weight: v })}
              />
              <label className="flex items-center gap-2 self-end rounded border border-border bg-surface/60 px-3 py-2">
                <input
                  type="checkbox"
                  checked={f.required}
                  disabled={readOnly}
                  onChange={(e) => setField(i, { required: e.target.checked })}
                  className="accent-signal"
                />
                <span className="text-sm text-zinc-200">Required</span>
              </label>
              {f.type === "enum" && (
                <div className="sm:col-span-2">
                  <Text
                    label="Options (comma-separated)"
                    value={csv(f.options)}
                    disabled={readOnly}
                    onChange={(v) => setField(i, { options: parseCsv(v) })}
                  />
                </div>
              )}
            </div>
            <div className="mt-2">
              <Text label="Question to ask" value={f.ask} disabled={readOnly} onChange={(v) => setField(i, { ask: v })} />
            </div>
          </div>
        ))}
      </div>

      {!readOnly && (
        <button
          onClick={add}
          className="rounded border border-border bg-surface px-3 py-2 text-sm text-zinc-200 hover:border-signal"
        >
          + Add field
        </button>
      )}
    </div>
  );
}

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded bg-surface px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-zinc-400">
      {children}
    </span>
  );
}

function IconBtn({
  children,
  label,
  onClick,
  danger,
}: {
  children: React.ReactNode;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      aria-label={label}
      title={label}
      onClick={onClick}
      className={`rounded border border-border px-2 py-1 text-xs ${
        danger ? "text-danger hover:border-danger" : "text-zinc-300 hover:border-signal"
      }`}
    >
      {children}
    </button>
  );
}

function labelCls() {
  return "block font-mono text-[11px] uppercase tracking-wider text-zinc-400";
}
const inputCls =
  "mt-1 w-full rounded border border-border bg-surface px-2 py-1.5 text-sm outline-none focus:border-signal disabled:opacity-60";

function Text({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <label className="block">
      <span className={labelCls()}>{label}</span>
      <input value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} className={inputCls} />
    </label>
  );
}

function Num({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
}) {
  return (
    <label className="block">
      <span className={labelCls()}>{label}</span>
      <input
        type="number"
        value={String(value)}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className={inputCls}
      />
    </label>
  );
}

function Select({
  label,
  value,
  options,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <label className="block">
      <span className={labelCls()}>{label}</span>
      <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} className={inputCls}>
        {options.map((o) => (
          <option key={o} value={o}>
            {o === "" ? "— none —" : o}
          </option>
        ))}
      </select>
    </label>
  );
}
