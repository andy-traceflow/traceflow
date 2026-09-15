# ADR-0006: Client-portal foundation, onboarding intake, and conversation closings

**Date:** 2026-07-22
**Status:** accepted

## Context

Phase 3+ calls for a client-facing surface: a customer's staff logging into a
company-scoped UI (`/manage`) with tenant-admin rights, and onboarding as a
**native** flow that writes to the DB and prompts account creation — replacing
the 77-field Jotform. The "no client-facing UI until Client 8" rule (CLAUDE.md)
still holds, so none of that UI ships now. But the *plumbing* underneath it was
missing in ways that would have forced a re-architecture later, and three of the
gaps were actively breaking production.

Four concrete problems:

- **No user → tenant resolution.** ADR-0004 deliberately reserved Supabase Auth
  for the client portal and built `admin_users` + HS256 for the founder surface.
  The Supabase side (`middleware/auth.py`'s `verify_jwt` / `require_permission`,
  the `user_permissions` table from migration 008) existed as scaffolding, but
  **nothing derived `client_id` from an authenticated principal** — tenant
  context was only ever set from a webhook URL path. So `/api/kb` and
  `/api/calculator`, which already depend on `verify_jwt`, returned zero rows or
  a 400 for any real user. Every portal route would hit this.
- **No create-client path.** `scripts/onboard_client.py` was a stub writing 6 of
  ~35 config columns; there was no API to create a tenant at all. A native form
  had nothing to call, and ~40% of the Jotform fields (address, DBA, website,
  GBP, structured owner/PoC contacts, tech inventory, logistics) had no column
  to land in.
- **A misread of the domain requirement.** The working assumption was that
  serving `/admin` and `/manage` required moving the domain off Namecheap. Path
  routing is app-side (FastAPI mounts); the registrar is irrelevant. The real
  decision was URL *topology*, which fixes CORS, cookie scope, and the Vite
  `base` path.
- **Conversations that never resolved.** Two prod defects, both silent:
  `Lead.notes` was declared non-optional against a nullable column, so
  `Lead(**row)` raised and 500'd every inbound SMS reply; and the qualifier made
  a single Messages API call, so a turn where the model returned only a
  `tool_use` block (normal `stop_reason: "tool_use"` behavior) produced no reply
  text and the conversation dead-ended. Separately, when code *did* terminate a
  conversation, nothing owned the closing message — whatever the model happened
  to emit was sent, in practice a bare "Got it!".

## Decision

Build the portal's **backend keystone and intake pipeline now**, keep the
client-facing UI deferred, and make conversation endings deterministic.

### 1. Portal auth keystone — resolve tenant from the principal

`middleware/portal_auth.py` adds `portal_principal`, a yielding FastAPI
dependency that: verifies the Supabase JWT (reusing `verify_jwt`), loads the
caller's `user_permissions` rows, resolves the **active** tenant, sets
`app.current_client_id` via `set_current_tenant`, and clears it in a `finally` —
the same set/reset discipline `tenant_resolver_middleware` uses for webhooks.

Two consequences worth stating explicitly:

- The membership lookup **must bypass RLS**. It is the query that decides which
  tenant to scope to, so it cannot itself be tenant-scoped (chicken-and-egg). It
  uses `get_service_connection()` filtered explicitly by `user_id`.
- A user may belong to several tenants (`user_permissions` PK is
  `(client_id, user_id)`). A single membership resolves implicitly; multiple
  memberships require an explicit `X-Client-Id` header, validated against the
  membership set — an unknown or non-member tenant is a 403, never a silent
  fallback to "first tenant".

This is **World B** in ADR-0004's terms. `admin_users` and `/api/admin` are
untouched; the two identity pools stay separate.

### 2. Onboarding is staged, then promoted — never auto-provisioned

Submissions land in `onboarding_submissions` (migration 022) — pre-tenant data
with no `client_id`, so it is **not** tenant-scoped. It is locked to the service
role exactly like `admin_users`: RLS enabled + FORCED with zero policies, i.e.
deny-all under `authenticated`. It stores the raw payload and a normalized
`mapped_config` draft, is form-agnostic (Jotform today, native form later), and
carries a `promoted_client_id` FK with `ON DELETE SET NULL` so off-boarding a
client doesn't erase the historical record.

A submission becomes a tenant only through an explicit founder action
(`POST /api/admin/onboarding-submissions/{id}/promote`). Auto-provisioning on
submit was rejected: a bad submission would mutate production tenant data with
no review gate.

`services/provisioning.py` is now the **single create-client path** — both the
admin promote action and the refactored CLI call `provision_client`, so the two
can't drift. It applies the safe-write pattern from
`admin/clients.py::update_client_config`: service-role connection, one
transaction, an `extra="forbid"` Pydantic spec (a stray key is a validation
error, never an injected column), and no secrets — `crm_credentials` /
`webhook_signing_secrets` remain out-of-band.

The orphan Jotform fields get **one JSONB block**, `client_configs.
business_profile` (migration 023), rather than ~15 sparse typed columns —
mirroring `brand` / `revenue_config`, read through accessors with defaults so a
partial object still behaves.

`services/provisioning.provision_portal_user` completes the loop: create the
Supabase Auth user (invite / magic-link — no password ever touches us) and
upsert a `user_permissions` row with `is_admin = true`, the client-scoped
tenant-admin the schema has modeled since migration 008.

### 3. URL topology: `app.traceflow.app`, one origin for the whole app

The app gets its own subdomain (Render custom domain + a Namecheap DNS record —
**no registrar migration**), leaving the marketing site on `traceflow.app`.
`TrustedHostMiddleware` is added but **opt-in**: it activates only when
`ALLOWED_HOSTS` is set, so existing deploys are unaffected until the app is
actually exposed on the new host.

### 4. Code owns termination, therefore code owns the closing

`should_terminate` already decided when a conversation was over. The closing
message now follows from that decision rather than from whatever the model
emitted on the terminal turn (`prompts/greeting.render_handoff` /
`render_decline`, templates in migration 025). Two endings, because they are
different promises:

- **handoff** (`qualified` / `needs_review`) — the lead is real; the caller is
  told a person will follow up. This is the product's core promise.
- **decline** (`disqualified` by a hard gate) — deliberately does *not* promise a
  callback. Telling an out-of-area caller someone will ring them, when nobody
  will, is worse than saying nothing.

The model's terminal-turn text is **replaced**, not appended: it is typically a
preamble ("Got it!") or a follow-up question the system will never process.

The qualifier itself now runs the standard tool-use continuation loop — when a
turn returns a `tool_use` block with no text, the `tool_result` goes back so the
model can write its SMS. It continues only while the reply is still empty
(looping on a turn that already produced text would send a duplicate SMS) and is
capped at 3 rounds.

## Consequences

**Good**

- The portal becomes a UI-on-top exercise: auth, tenant scoping, provisioning,
  and account creation all exist and are tested headlessly, with no client-facing
  surface shipped and the Client-8 rule intact.
- `/api/kb` and `/api/calculator` — dormant since they were written — work for a
  real user the moment a portal route mounts.
- One create-client path for CLI and UI; one review gate before anything reaches
  live tenant tables.
- A conversation now always ends with the caller knowing what happens next.

**Costs / risks**

- `onboarding_submissions` is a second service-role-only table, so it inherits
  `admin_users`' property that a service-role bug is not caught by RLS. The
  isolation tests do not cover it (it has no `client_id` to isolate on);
  correctness rests on the admin gate.
- The multi-tenant portal user needs an active-tenant convention
  (`X-Client-Id`); a future portal UI must send it or the request 400s. That is
  deliberate — guessing would be worse.
- `provision_portal_user` depends on Supabase's Admin API being reachable and on
  SMTP being configured for invites; neither is exercised in CI (the call is
  stubbed).
- The closing replaces model text unconditionally on a terminal turn. If a
  future termination reason wants a bespoke ending, it needs its own template
  rather than falling back to the model.

## Notes

- **`max_turns` counts messages, not exchanges.** `turn_count` increments on
  every message in both directions, and `should_terminate` compares it to
  `schema.max_turns` — so `max_turns: 8` yields roughly four caller replies. This
  is existing behavior, left unchanged here, but it is the reason a conversation
  can terminate at ~50% completeness. Raise `max_turns` per client, or change the
  comparison to count inbound turns, as a separate decision.
- **Deferred, explicitly:** the `/manage` SPA and native onboarding form (Client
  8 gate), self-serve/OAuth onboarding (Phase 4), and widening
  `admin_users.role` for internal RBAC.
