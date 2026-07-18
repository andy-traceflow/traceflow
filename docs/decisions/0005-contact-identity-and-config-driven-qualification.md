# ADR-0005: Contact identity, source-of-truth resolution, and config-driven qualification

**Date:** 2026-07-15
**Status:** accepted

## Context

ADR-0002 built the caller-classification runtime, but it reasoned about callers
one lead at a time. Phone lived on `leads`, so a caller's memory died with the
lead. Three concrete bugs fell out of that:

- **Amnesia.** Once a lead hit any terminal status, a callback from the same
  number created a fresh, context-free lead and re-asked everything.
- **Silent SMS drop.** `_process_sms_reply` matched an open lead by phone; an
  inbound SMS with no open lead (a qualified lead texting a follow-up, or a cold
  inbound with no prior call) was logged and ignored.
- **Redundant lookups.** A known repeat caller paid for a fresh CRM + Twilio
  Lookup on every call.

Two more structural problems: (1) "what is this caller" was encoded in four
overlapping vocabularies (`leads.classification`, the routing `Route`,
`crm_contact.ContactType`, a proposed `contacts.contact_type`) with no record of
*who decided*, so an AI inference could silently overwrite a human decision; and
(2) qualification was hardcoded — a literal tool schema and a fixed field list in
`prompts/qualifier.py`, with `leads` columns for every field and nowhere to put
a client-specific one (material, project stage, property type).

## Decision

Introduce a durable **contact** as the identity above the lead, resolve "what is
this caller" through a **config-driven source-of-truth resolver**, and make
qualification a **per-client config schema** with code-owned termination — all
within the existing single lifecycle, no per-client branches.

- **`contacts` table (migration 018).** One row per `(client_id, phone)`, RLS
  enabled + forced + NULL-safe policy like every tenant table (isolation suite
  grows 13 → 14 tables). `leads.contact_id` links up to it (`ON DELETE SET
  NULL` — the lead is the business record, the contact is the index). Backfilled
  from existing leads.

- **ONE vocabulary with provenance and a precedence rule.** `contact_type` ∈
  {`unknown`, `prospect`, `customer`, `vendor`, `spam`, `blocked`} with
  `contact_type_source` ∈ {`manual`, `crm`, `inferred`}. Precedence **manual >
  crm > inferred** is enforced in exactly one place (`services/contacts
  .set_contact_type`, the sole writer): an inferred write never clobbers a
  crm/manual value; a crm write never clobbers manual; `blocked` is settable only
  with `source='manual'` and is never overwritten by any classifier. `unknown`
  vs `prospect` is not cosmetic — a known prospect is never spam-scored;
  `spam` (revocable inference) vs `blocked` (human decision) is not cosmetic
  either. Every type change drops a `contact_type_changed` event.

- **Config-driven source-of-truth resolver (migration 019, the ADR-0003 pattern
  applied to identity).** `client_configs.contact_config.source_of_truth` ∈
  {`auto`, `crm`, `traceflow`} is resolved to a concrete mode at runtime in ONE
  place (`services/contacts.resolve_contact_type`). `auto` → `crm` when the
  provider has a registered adapter *and* credentials, else `traceflow` (a CRM
  with no adapter, e.g. ServiceTitan, lands on `traceflow` with no special
  casing). In `crm` mode the resolver is cache-first (a fresh crm-typed contact
  needs no network call) and degrades to the local row on any lookup miss; in
  `traceflow` mode the local `contacts` row IS the authority. **The `contacts`
  table is always populated — the cache in one mode, the ledger in the other.**
  There is no second no-CRM code path; nothing outside the resolver branches on
  the mode.

- **Time-bounded routing.** Resume/reopen windows (`conversation_config`) turn
  the phone-keyed, unbounded open-lead query into a contact-keyed, time-bounded
  one: a fresh open lead → `active_conversation`; a stale one → `resumed_
  conversation` (reuse the lead, no duplicate CRM record); a terminal contact
  within the reopen window → `returning_contact` (new lead seeded with the
  contact's person facts). An inbound SMS with no open lead now opens one.

- **Config-driven qualification (migration 020).** `client_configs
  .qualification_schema` (a validated `QualificationSchema`) replaces the
  hardcoded field list. Each field declares type/scope/weight/`ask`/`depends_on`/
  `maps_to`. **Code owns termination** (`services/qualification`): the model only
  asks the next question and records extractions; completeness, hard gates
  (service-area, `disqualify_if`), and the qualified/needs_review/disqualified
  decision are deterministic. `qualification_score` is repurposed as
  completeness; a separate `value_score` estimates job value — the two are
  **never blended**. Non-canonical fields land in `leads.qualification_data` and
  can push to a CRM custom field via a dotted-path mapping.

- **Unified prompt context + rolling summary (migration 021).** One context
  object (`prompts/context.py`) renders `<business>` (cached), `<caller>`,
  `<state>`, `<time>` for all three prompts; a rolling `contacts.summary` is
  written on terminal transition so the next call arrives with context.

- **Admin surface (Slice 5).** Config editors expose every new block
  (`qualification_schema` validated → 422 on a bad shape); contact
  list/detail/manual-retype endpoints (retype is the sole `manual`/`blocked`
  writer, audit-logged). The React config panel surfaces all of it.

## The prime directive (unchanged, extended)

**A lookup failure must NEVER drop a real lead, and an AI inference must never
overwrite a human decision.** Every failing/ambiguous path still degrades toward
`potential_lead`; additionally, the precedence rule guarantees `manual`
(including `blocked`) survives any classifier, scorer, or CRM sync, and CRM
write-back of inferred types is off by default and manual-only.

## Alternatives considered

- **A `conversations` table.** Rejected — lead-as-conversation works;
  conversation state is derived in code from `messages` + the new `leads`
  activity columns. No new table.
- **Separate no-CRM lifecycle/routing.** Rejected outright (violates the
  platform thesis). The source-of-truth resolver is the seam; `if
  config.crm_provider:` anywhere outside it is a design break.
- **Keeping qualification hardcoded, or storing it as free text**
  (`qualification_prompt`). Rejected — the free-text column was the wrong
  primitive and nothing read it; it is now marked DEPRECATED.
- **Blending completeness and value into one score.** Rejected — a fully
  captured $700 backsplash is 100% complete and near-zero value; one number
  would destroy the digest's credibility.

## Consequences

### Positive
- Returning callers arrive with context; cold-inbound SMS is never dropped;
  known callers cost zero redundant lookups. Caller memory is durable.
- One vocabulary with provenance; human decisions are tamper-proof by construction.
- Qualification is per-client config, not code — a new field is a config row.
- No-CRM and with-CRM tenants run the identical code path.

### Negative
- More per-call DB work (contact resolve/upsert + type persistence) on the hot path.
- CRM write-back of manual types is a reserved seam (no adapter implements the
  outward write yet); `blocked` is manual-only, so a bad automated tag can't self-heal.
- The demo's contacts are 1 lead each (no multi-lead returning-caller showcase yet).

### Reversibility
High. All columns/tables are additive; `source_of_truth` defaults `auto` and a
tenant with no `contact_config` resolves exactly as the CRM-present/absent case
did. Default-config tenants behave identically to pre-018 on every path except
the three fixed bugs.

## Decision review trigger

Revisit if: a client needs deal-level contact history (multi-lead rollups);
write-back of contact types becomes a real requirement (implement the adapter
seam); or the resume/reopen/cache windows prove wrong against real call cadence.

## Implementation notes

- Migrations 018–021 (applied to prod via the Supabase MCP 2026-07-15, recorded
  in `schema_migrations`; advisors clean).
- Code: `models/contact.py`, `models/qualification.py`; `services/{contacts,
  phone,qualification,classification}.py`; `prompts/{context,greeting,intent,
  qualifier,summarize}.py`; `webhooks/twilio.py`; `routers/admin/contacts.py` +
  config editors; `scripts/export_contacts.py`; `admin-ui/` config panel +
  `QualificationEditor.tsx`.
- Tests: ~200 added across contacts/phone/qualification/resolver/classification/
  twilio/context/summarize/admin/demo + golden conversation fixtures. Suite 521
  passed / 43 skipped.
- Extends ADR-0002 (caller classification) and mirrors ADR-0003's
  `revenue_config.mode` provenance pattern for identity.
