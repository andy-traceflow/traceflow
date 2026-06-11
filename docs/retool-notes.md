# Retool Admin Notes

> **SUPERSEDED 2026-06-10** by the self-hosted admin surface — see
> [ADR-0004](decisions/0004-self-hosted-admin-api.md). The admin tool is now
> `/api/admin/*` + the SPA at `/admin` in this repo; Retool was never stood up.
> Kept for history: the client-switcher convention and the explicit-`client_id`
> query discipline below carried straight into the new design.

Operational notes for the founder-only Retool admin app — the
"monitoring layer" that gives the LLR pipeline visibility before
Client 1 goes live. Companion to the Retool Admin UI panel spec.

## Setup

### Postgres connection

Retool connects to the TraceFlow Supabase project with **admin
credentials** (the service-role DSN, same one `SUPABASE_DB_URL` uses on
Render). That deliberately bypasses RLS — admin tooling needs to see
across tenants. Protection lives at the Retool layer (single-user
auth + admin-only deploy URL), not in the database.

Connection string: pooler in **session mode** (port 5432). Get the
current value from Supabase Dashboard → Connect → "Session pooler." Do
**not** use the transaction pooler (port 6543) — Retool's session
state can break across queries.

### Admin endpoint auth

For operations that need application logic (re-push to CRM), Retool
calls the FastAPI app at `/api/admin/...`. Auth is a long-lived bearer
token verified by `middleware.auth.verify_admin_token`.

The token is either:
- The bare `ADMIN_JWT_SECRET` value — simplest. Paste it into a Retool
  REST API Authorization header as `Bearer <secret>`. Rotate the
  secret in Render to revoke.
- An HS256 JWT signed with `ADMIN_JWT_SECRET` — rotatable per-token
  via the standard `exp` claim. Mint with any JWT tool.

Either works; the verifier accepts both. The bare-secret path is the
fast lane for a single-founder admin tool.

## The Client Switcher Convention

Panel 0 (the client dropdown at the top of the app) is the single
source of truth for the active tenant. **Every other query must
filter by `client_id` explicitly** — Retool bypasses RLS, so the
database will not enforce isolation. A missing `WHERE client_id = ...`
is a cross-tenant leak.

When code-reviewing a new Retool query, check for the `client_id`
filter first.

**Do not use `{{ client_switcher.value }}` (or any other `{{ }}`
interpolation) inside SQL query bodies** — it triggers a silent
failure in Retool's PG connector. See the next section.

## SQL parameterization gotcha — avoid `{{ }}` in SQL bodies

Retool's PostgreSQL connector defaults to prepared statements. For
queries like `SELECT $1::uuid AS x` the PG driver can't infer the
parameter type, errors with "could not determine data type of
parameter," and **Retool swallows the error and displays "No results
returned" with no visible message.** This burned half a session to
diagnose; documenting so we don't repeat it.

**Standing convention: SQL query bodies use literal values only — no
`{{ }}` interpolation.** Two viable patterns:

1. **Hardcode the `client_id`** in each query. For founder-only admin
   tooling with a handful of tenants this is fine — maintain one
   query per active tenant, or edit the query inline when switching.
   Verbose but obvious; nothing magic to debug.

2. **Disable prepared statements at the resource level** if you need
   dynamic filtering by switcher selection: Resources →
   `traceflow_supabase` → Edit → Advanced → toggle **"Disable
   converting queries to prepared statements"** ON. After that, `{{ }}`
   inlines the value as a literal string into the SQL and the
   `{{ client_switcher.value }}` pattern works as expected.
   **Caveat:** SQL-injection-vulnerable on any free-text input. Safe
   for UUIDs from your own dropdown; never use it on user-typed text.
   Don't expose any app using this resource to non-founders.

The `{{ }}` restriction applies **only to SQL query bodies**. REST
query URLs and headers (e.g. `{{ admin_api_base }}/...` for the
re-push endpoint) are fine — that's plain string templating, not
parameterization.

When in doubt, hardcode and move on.

## audit_log gotcha — the CHECK constraint

The original panel spec shows audit_log INSERTs with operation values
like `'update_config'` and `'manual_ai_usage_reset'`. **Those will
fail.** The `audit_log.operation` column has a CHECK constraint
allowing only: `'create' | 'update' | 'delete' | 'login' | 'export' |
'sync'`.

The correct pattern: use one of the six allowed values for `operation`
and encode the specific action in `target_table` + `snapshot`. For
example, Panel 1's config save becomes:

```sql
-- Replace the two UUID literals with the active client's id (or
-- flip the prepared-statement resource toggle and use
-- {{ client_switcher.value }} — see SQL parameterization gotcha
-- above for the tradeoff).
INSERT INTO audit_log (client_id, actor, operation, target_table, target_id, snapshot)
VALUES (
  '2a94b206-12c8-4af9-8dd6-29da32c363e6'::uuid,  -- client_id
  'founder_retool',
  'update',                                       -- not 'update_config'
  'client_configs',
  '2a94b206-12c8-4af9-8dd6-29da32c363e6'::uuid,  -- target_id (same client)
  '{"panel":"client_config_editor","fields":{}}'::jsonb
);
```

Panel 5's AI usage reset is the same shape: `operation = 'update'`,
`target_table = 'client_configs'`, snapshot
`{panel: 'ai_usage_meter', reset: true, reason: '...'}`.

Note: the `leads`, `kb_entries`, and `client_configs` tables also
have trigger-based audit logging that auto-records every
INSERT/UPDATE/DELETE. The explicit Retool-side INSERT is the *intent*
layer (who did it via what panel); the trigger captures the row-level
diff.

## `/api/admin/leads/{lead_id}/repush`

The only application-logic admin endpoint at the moment.

- **Auth:** `Authorization: Bearer <ADMIN_JWT_SECRET>` (or HS256 JWT).
- **Path:** `POST /api/admin/leads/{lead_id}/repush` (UUID `lead_id`).
- **Body:** none.
- **Behavior:**
  - If the lead has no `external_id`, runs the adapter's `push_lead` —
    same code path as the original push, just invoked manually. Handles
    the "original push failed" case.
  - If the lead already has an `external_id`, runs `update_lead` with
    the current canonical fields — syncs CRM to the latest qualifier
    extractions. **Never creates a duplicate** CRM record.
- **Response:** `{lead_id, client_id, provider, action, external_id}`.
- **Errors:** 404 (lead not found), 400 (no client_config, no
  `crm_provider`, or unknown provider), 503 (admin auth not
  configured), 5xx (adapter raised — check Render logs for the CRM
  error body).
- **Audit:** records an `audit_log` row with `operation='sync'`,
  `actor='founder_retool'`, `target_table='leads'`, snapshot
  `{action, provider, external_id}`.

Retool wiring: REST query, method `POST`, URL
`{{ admin_api_base }}/api/admin/leads/{{ selected_lead.id }}/repush`,
Authorization header pulled from a Retool resource secret.

### Sanity check with curl

Once `ADMIN_JWT_SECRET` is set in Render and a lead exists:

```sh
curl -X POST "https://traceflow-api-XXXX.onrender.com/api/admin/leads/$LEAD_ID/repush" \
  -H "Authorization: Bearer $ADMIN_JWT_SECRET"
```

Expect `{"lead_id": "...", "action": "push" | "update", ...}`. A 4xx
means the lead/config isn't there or no provider is set; a 5xx means
the adapter call to the CRM raised — check the Render logs.

## Form-reset gotcha — Panel 1

Retool's form inputs do **not** auto-repopulate when the client
switcher changes. You'll see stale values from the previous tenant.
Add a "reset to current values" event handler on the form that runs
`get_client_config`, then sets each input's `value` property from the
result. Wire it to the client switcher's `change` event.

## `is_test` column on leads

Migration 012 adds `is_test BOOLEAN NOT NULL DEFAULT FALSE` to
`leads`. Wire the "Mark as test" button on Panel 4 to a direct SQL
update from Retool (chain it with an audit_log INSERT — operation
`'update'`, target_table `'leads'`, snapshot `{is_test: true}`). No
admin endpoint needed for this — it's a one-field UPDATE.

Filter test leads out of dashboards with `WHERE is_test = false`.

## What not to do

Reaffirming the panel spec's own list, in priority order:

- **Don't add cross-tenant views.** "All leads across all clients" is
  a dashboard, not a tool. You don't need it yet.
- **Don't build charts.** Metrics belong in the daily digest email,
  not the admin UI. Retool's chart components are seductive; resist.
- **Don't add a "create new client" flow.** The provisioning script is
  the source of truth — duplicating it in Retool means weaker safety
  guarantees.
- **Don't edit leads directly.** Read-only is the right policy.
  Re-push, mark-as-test, and (eventually) merge-duplicate are the only
  write operations on a lead.
- **Don't expose Retool to clients.** Founder-only. Auth it at the
  Retool layer (single password or your SSO) and never share the URL.
