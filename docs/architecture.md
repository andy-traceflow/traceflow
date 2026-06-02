# TraceFlow Architecture

**Status:** Phase 0 — platform skeleton built, deployed to Render against live Supabase, integration tests passing
**Last updated:** 2026-05-20

---

## Core thesis

TraceFlow is **one platform, configured per tenant**. Not custom builds. This document specifies how that's enforced at every layer.

See [`CLAUDE.md`](../CLAUDE.md) for the operating principles. This file covers the technical realization.

---

## System overview

```
┌────────────────────────────────────────────────────────────────┐
│  External event sources                                         │
│  Twilio · Shopify · CRM webhooks · Website forms · Email       │
└────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────┐
│  FastAPI (single Render service)                                │
│  /webhooks/twilio/missed-call/{client_id}                       │
│  /webhooks/shopify/{client_id}                                  │
│  /webhooks/crm/{provider}/{client_id}                           │
│  /webhooks/generic/{client_id}/{slug}                           │
│  /api/admin/* (internal)                                        │
│  /api/portal/* (client-facing, Phase 3+)                        │
└────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────┐
│  Tenant resolver middleware                                     │
│  Sets app.current_client_id for RLS enforcement                 │
└────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────┐
│  Domain layer (Python)                                          │
│  - Lead processing pipeline                                     │
│  - AI qualification (Anthropic API)                             │
│  - Knowledge base retrieval (pgvector)                          │
│  - Adapter dispatch (CRM, e-comm, messaging)                    │
└────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────┐
│  Supabase (single project, RLS-isolated)                        │
│  Postgres + pgvector + Auth + Storage                           │
└────────────────────────────────────────────────────────────────┘
                              ↓ (outbound)
┌────────────────────────────────────────────────────────────────┐
│  External destinations                                          │
│  Twilio SMS · CRM APIs · Email · Owner notifications           │
└────────────────────────────────────────────────────────────────┘
```

---

## Multi-tenancy model

### Tenant identification

Every webhook URL contains the `client_id` in its path:

```
https://api.traceflow.app/webhooks/twilio/missed-call/{client_id}
https://api.traceflow.app/webhooks/shopify/{client_id}
https://api.traceflow.app/webhooks/crm/ghl/{client_id}
https://api.traceflow.app/webhooks/generic/{client_id}/{slug}
```

The `client_id` is a non-secret UUID. Authenticity is verified via per-client webhook signing secrets, not by obscuring the ID.

For payload-based identification (e.g., a Twilio webhook where the path doesn't carry the ID), the resolver falls back to looking up the tenant by the destination phone number, Shopify shop domain, etc.

### Tenant resolver middleware

FastAPI middleware runs before every request (`src/app/middleware/tenant_resolver.py`):

1. Extract `client_id` from the URL path via regex (path-based for every webhook URL we publish)
2. For `/webhooks/*` paths: verify the webhook signature using the per-client secret from `client_configs.webhook_signing_secrets`
3. Set the request-scoped `_current_tenant` ContextVar to the resolved `client_id`
4. Hand off to the route handler
5. On exit, reset the ContextVar

Path patterns the regex recognizes (see `tests/test_tenant_resolver.py` for the full matrix):

```
/webhooks/twilio/{channel}/{client_id}        # e.g. /webhooks/twilio/sms/<uuid>
/webhooks/shopify/{client_id}
/webhooks/crm/{provider}/{client_id}
/webhooks/generic/{client_id}/{slug}
```

Anything not matching (e.g. `/health`, `/docs`, `/api/kb`) passes through with no tenant context — those routes handle their own auth or are public.

### How tenant context actually reaches the database

The ContextVar set by the middleware is read inside `db.get_connection()`. Each connection acquisition opens a transaction, switches roles, sets the session variable, and yields the connection to the caller. When the caller's `async with` block exits, the transaction ends and everything reverts — no state can leak into the next request that acquires the same pooled connection.

```python
async with _pool.acquire() as conn:
    async with conn.transaction():
        await conn.execute("SET ROLE authenticated")
        client_id = _current_tenant.get()
        if client_id is not None:
            await conn.execute(
                "SELECT set_config('app.current_client_id', $1, true)",
                str(client_id),
            )
        yield conn
```

### Why `SET ROLE authenticated` (the BYPASSRLS gotcha)

**This is non-obvious and was caught by the live tenant isolation test suite, not by code review.** Supabase ships its `postgres` role with the `bypassrls=true` attribute. PostgreSQL skips RLS *entirely* for any role with that attribute — regardless of `ENABLE ROW LEVEL SECURITY` or even `FORCE ROW LEVEL SECURITY` on the table. The FastAPI service connects via `SUPABASE_DB_URL` as `postgres`, so without further action, every query is unfiltered admin access.

The fix is to `SET ROLE authenticated` per-request. The `authenticated` role has `bypassrls=false` and Supabase grants it full DML on every public table by default. The role switch is transaction-bounded so it reverts when the request ends.

Verify on any Supabase project with:

```sql
SELECT rolname, rolbypassrls FROM pg_roles
WHERE rolname IN ('postgres', 'authenticated', 'anon', 'service_role');

-- Expected:
--  postgres      | t   ← bypasses RLS
--  service_role  | t   ← bypasses RLS (intended for backend admin)
--  authenticated | f   ← respects RLS
--  anon          | f   ← respects RLS
```

### Row Level Security

Every tenant-scoped table has:

```sql
ALTER TABLE <table_name> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <table_name> FORCE  ROW LEVEL SECURITY;   -- migration 010

CREATE POLICY tenant_isolation ON <table_name>
    FOR ALL
    USING (client_id = NULLIF(current_setting('app.current_client_id', true), '')::uuid);
```

Three details that matter:

- **`FORCE ROW LEVEL SECURITY`** (migration 010) — without this, the table *owner* bypasses RLS even when their role has `bypassrls=false`. Belt and suspenders.
- **`NULLIF(..., '')`** (migration 011) — `current_setting('foo', true)` returns NULL when unset BUT returns `''` when explicitly cleared. Casting `''` to UUID crashes. NULLIF makes the "no tenant context" path silently return zero rows instead of erroring.
- **`true` (missing_ok) argument** to `current_setting` — keeps the policy expression safe when the setting was never set at all (returns NULL, comparison is NULL, row excluded).

### Tests (non-negotiable, and running)

`tests/test_tenant_isolation.py` runs against the live database in CI (and locally with `TRACEFLOW_TEST_DB_URL` set):

- 13 tenant-scoped tables parametrized: every one must have RLS enabled AND a policy
- Direct cross-tenant isolation tests for `leads`, `kb_entries`, `messages`, `events` — insert as A, switch to B, assert zero rows
- "No tenant context → deny all reads" — the empty-string defense path
- Test bodies `SET ROLE authenticated` to actually exercise RLS (mirrors production code path)

`tests/test_tenant_resolver.py` (28 tests) covers the path-extraction regex for every webhook URL shape — positive/negative cases, partial UUIDs, case sensitivity, trailing slashes.

`tests/test_generic_webhook.py` (16 tests) is the first full integration suite that goes through the FastAPI app via `TestClient` against the live DB. Caught a latent JSONB-codec bug that unit tests had missed.

---

## Canonical schema

The internal representation every component agrees on. All adapters translate to/from this.

```python
class Lead(BaseModel):
    id: UUID
    client_id: UUID
    external_id: str | None         # ID in client's CRM after push
    source_system: str              # 'twilio_missed_call', 'shopify', 'website_form', 'manual', etc.
    
    contact_name: str | None
    phone: str | None
    email: str | None
    address: str | None
    
    service_type: str | None        # 'countertop', 'flooring', 'pool_resurface', etc.
    sqft: float | None
    budget_range: str | None        # '<5k' | '5k-15k' | '15k-50k' | '50k+' | None
    timeframe: str | None           # 'asap' | 'this_month' | 'this_quarter' | 'researching' | None
    
    qualification_status: str       # 'unqualified' | 'qualified' | 'high_value' | 'spam'
    qualification_score: int        # 0-100, computed by AI
    
    notes: str
    conversation_transcript: list[Message]
    raw_payload: dict               # ALWAYS preserve original
    
    created_at: datetime
    qualified_at: datetime | None
    pushed_to_crm_at: datetime | None
```

`raw_payload` is non-negotiable. Debugging an integration failure without the original webhook body is misery.

---

## The three-layer integration model

### Layer 1: Adapters

One adapter per supported external system, conforming to a uniform interface:

```python
class CRMAdapter(Protocol):
    async def push_lead(self, lead: Lead, config: ClientConfig) -> str: ...
    async def update_lead(self, external_id: str, updates: dict, config: ClientConfig) -> None: ...
    async def parse_webhook(self, payload: dict, config: ClientConfig) -> Lead: ...
    # Best-effort caller classification (lead lifecycle Section 3); returns
    # None when unsupported/not found/on error — never raises.
    async def lookup_by_phone(self, phone: str, config: ClientConfig) -> CRMContact | None: ...

class GoHighLevelAdapter:
    async def push_lead(self, lead: Lead, config: ClientConfig) -> str:
        # GHL REST API specifics
        ...

class HubSpotAdapter:
    async def push_lead(self, lead: Lead, config: ClientConfig) -> str:
        # HubSpot API specifics
        ...

class MondayAdapter:
    async def push_lead(self, lead: Lead, config: ClientConfig) -> str:
        # GraphQL mutations (reuse SEMCO code)
        ...

ADAPTER_REGISTRY: dict[str, CRMAdapter] = {
    'ghl': GoHighLevelAdapter(),
    'hubspot': HubSpotAdapter(),
    'monday': MondayAdapter(),
}
```

**Build order (do not build speculatively):**
1. GoHighLevel (Phase 0 default; 40% affiliate)
2. Monday (reuse SEMCO code)
3. Generic webhook handler ✅ — built, signature-verified, end-to-end tested (`src/app/webhooks/generic.py`, 16 integration tests)
4. HubSpot (when first HubSpot client signs)
5. Jobber / ServiceTitan (when 3+ clients request)

### Layer 2: Field mappings

Two clients on the same CRM can have different schemas because of custom fields. Mappings live in the database:

```sql
create table client_field_mappings (
  client_id uuid not null references clients(id),
  integration text not null,           -- 'crm', 'shopify', 'website_form'
  canonical_field text not null,       -- 'sqft', 'service_type', 'phone'
  external_field text not null,        -- 'project_size_sqft' (their field name)
  external_field_type text,            -- 'standard' | 'custom_field' | 'custom_property'
  transform jsonb,                     -- optional value translation rules
  primary key (client_id, integration, canonical_field)
);
```

Adapter code reads these at runtime — field names are never hardcoded.

The `transform` JSONB column handles value translation:

```json
{
  "type": "value_map",
  "mapping": {
    "countertop": "Kitchen Counter",
    "flooring": "Floor Installation"
  }
}
```

Supported transform types: `value_map`, `regex_replace`, `numeric_scale`, `concatenate`, `split`.

### Layer 3: Generic webhook config

The escape hatch for long-tail systems where building a full adapter isn't justified. **Built and integration-tested as of 2026-05-20.**

```sql
create table client_webhook_configs (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references clients(id),
  webhook_slug text not null,            -- 'jobber-lead-created', 'custom-form-x'
  parser_type text not null,             -- 'jsonpath' | 'jq' | 'python_template'
  field_extractors jsonb not null,       -- canonical_field → extraction expression
  signing_secret text,                   -- nullable when signing_algorithm = 'none'
  signing_algorithm text not null,       -- 'hmac_sha256' | 'hmac_sha256_timestamped' | 'none'
  signature_header text,                 -- header name carrying the signature
  unique (client_id, webhook_slug)
);
```

Example config:

```json
{
  "parser_type": "jsonpath",
  "field_extractors": {
    "contact_name": "$.customer.full_name",
    "phone": "$.customer.phone_primary",
    "service_type": "$.job.category_label",
    "sqft": "$.job.dimensions.area_sqft"
  },
  "signing_secret": "<32+ random chars>",
  "signing_algorithm": "hmac_sha256",
  "signature_header": "X-Signature"
}
```

**Supported signing algorithms:**

- `hmac_sha256` — body-hash HMAC. Handler accepts either hex or base64 in the header (tries both before failing) so providers using either convention work without config changes.
- `hmac_sha256_timestamped` — Stripe-style `t=<ts>,s=<hex>` header. Body is signed as `{ts}.{body}`. Timestamps older than 300s are rejected as replays.
- `none` — explicit escape hatch for homegrown systems that genuinely cannot sign. Loud-named so config reviewers catch it on purpose.

**Parser types:**

- `jsonpath` — wired, used in tests. Multi-match expressions return the first value.
- `jq` — reserved (returns empty until the first client requests it; would require adding `jq` Python package as a dep).
- `python_template` — reserved (sandboxed eval is non-trivial; deferred until needed).

---

## Core schema (initial tables)

```sql
-- Tenants
create table clients (
  id uuid primary key default gen_random_uuid(),
  slug text unique not null,
  business_name text not null,
  status text not null default 'active',  -- 'active' | 'paused' | 'churned'
  tier text not null,                      -- 'founding_partner' | 'standard' | 'pro' | 'full_stack'
  signed_at timestamptz not null default now(),
  launched_at timestamptz,
  churned_at timestamptz,
  created_at timestamptz not null default now()
);

create table client_configs (
  client_id uuid primary key references clients(id) on delete cascade,
  business_hours jsonb not null,           -- per-day open/close
  service_area_zips text[],
  twilio_number text unique,
  crm_provider text,                       -- 'ghl' | 'hubspot' | 'monday' | 'generic'
  crm_credentials jsonb,                   -- encrypted
  webhook_signing_secrets jsonb,           -- per-integration
  brand jsonb,                             -- logo_url, primary_color, tone_of_voice, etc.
  vip_keywords text[],
  qualification_prompt text,               -- templated, with variable slots
  greeting_template text,
  ai_interaction_cap_monthly int not null default 1000,
  ai_interactions_used int not null default 0,
  ai_period_resets_at timestamptz not null default date_trunc('month', now() + interval '1 month'),
  updated_at timestamptz not null default now()
);

-- Field mappings + webhook configs (see Layer 2 / 3 above)
-- client_field_mappings, client_webhook_configs

-- Tenant-scoped data
create table leads (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references clients(id) on delete cascade,
  external_id text,
  source_system text not null,
  contact_name text,
  phone text,
  email text,
  address text,
  service_type text,
  sqft numeric,
  budget_range text,
  timeframe text,
  qualification_status text not null default 'unqualified',
  qualification_score int,
  notes text,
  raw_payload jsonb not null,
  created_at timestamptz not null default now(),
  qualified_at timestamptz,
  pushed_to_crm_at timestamptz
);

create index leads_client_created on leads(client_id, created_at desc);

create table messages (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references clients(id) on delete cascade,
  lead_id uuid not null references leads(id) on delete cascade,
  direction text not null,                 -- 'inbound' | 'outbound'
  channel text not null,                   -- 'sms' | 'email' | 'chat'
  body text not null,
  ai_generated boolean not null default false,
  raw_payload jsonb,
  created_at timestamptz not null default now()
);

create index messages_lead_created on messages(lead_id, created_at);

create table events (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references clients(id) on delete cascade,
  event_type text not null,                -- 'missed_call', 'sms_sent', 'crm_pushed', etc.
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create index events_client_type_created on events(client_id, event_type, created_at desc);

-- Knowledge base (for SIA Module C)
create table kb_documents (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references clients(id) on delete cascade,
  source_type text not null,               -- 'tds_sheet', 'install_guide', 'faq', 'manual'
  title text not null,
  source_url text,
  raw_content text,
  created_at timestamptz not null default now()
);

create table kb_chunks (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references clients(id) on delete cascade,
  document_id uuid not null references kb_documents(id) on delete cascade,
  chunk_index int not null,
  content text not null,
  embedding vector(1536),                  -- OpenAI text-embedding-3-small dimension
  metadata jsonb,
  created_at timestamptz not null default now()
);

create index kb_chunks_embedding on kb_chunks using ivfflat (embedding vector_cosine_ops);

-- Apply RLS to every tenant-scoped table
alter table client_configs enable row level security;
alter table client_field_mappings enable row level security;
alter table client_webhook_configs enable row level security;
alter table leads enable row level security;
alter table messages enable row level security;
alter table events enable row level security;
alter table kb_documents enable row level security;
alter table kb_chunks enable row level security;

-- (Policies created per table; see Multi-tenancy section above)
```

---

## Configuration vs customization in practice

When a client requests something that feels custom, ask: **can this be a config field instead of code?**

| Client request | Wrong solution | Right solution |
|---|---|---|
| "Add my phone at the end of the greeting" | `if client_id == 'X': append_phone()` | `greeting_template` config with `{{phone}}` placeholder |
| "Only alert me for $10K+ jobs" | Hard-coded threshold in code | `vip_keywords` + `vip_value_threshold` in `client_configs` |
| "Skip jobs outside zip codes 891xx" | Custom filter for this client | `service_area_zips` array filter applied universally |
| "Don't ask qualifying questions on weekends" | Custom cron logic | `business_hours` config respected by AI flow |
| "Use 'Estimate' instead of 'Quote' in messages" | Custom prompt for this client | `terminology_overrides` JSONB in config |

The pattern is always: **what variability exists across clients in this dimension? Push that into a config field. Build the feature once.**

---

## What to NOT build yet

Premature complexity kills momentum. The following are explicitly deferred:

- **Client-facing UI** — until Client 8 minimum. Email digests + Loom walkthroughs suffice in Phase 0–1.
- **Internal admin UI** — until Client 3 and you've felt the SQL pain. Then Retool, not custom.
- **Multi-region deployment** — until performance demands it (you won't hit this in Year 1).
- **Microservices** — never speak of this.
- **Custom email infrastructure** — Resend or Postmark covers it.
- **Self-serve onboarding** — Phase 4 strategic decision; not now.
- **Mobile apps** — clients don't need them; ops happens via web/email/SMS.

When in doubt, refer to the UI Maturity Model in PRD §11.
