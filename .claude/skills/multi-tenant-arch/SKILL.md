---
name: multi-tenant-arch
description: Patterns and rules for TraceFlow's multi-tenant architecture. Load when working on database schema, RLS policies, webhook routing, tenant resolver middleware, or any change that touches how requests are scoped to a client. Critical for preventing cross-tenant data leaks.
---

# Multi-Tenant Architecture

TraceFlow runs on a **shared-everything, RLS-isolated** model. One Render service, one Supabase project, many tenants. This skill governs how to keep that model safe and scalable.

## The non-negotiables

1. **Every tenant-scoped table has a `client_id` column.**
2. **Every tenant-scoped table has RLS enabled with a `tenant_isolation` policy.**
3. **Every request that touches tenant data sets `app.current_client_id` via middleware before query execution.**
4. **Every API endpoint has a test that verifies it cannot access another tenant's data.**

If any of these are missing, the work is incomplete. Do not merge.

## Schema patterns

### Adding a new tenant-scoped table

```sql
create table <table_name> (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references clients(id) on delete cascade,
  -- ... domain fields ...
  created_at timestamptz not null default now()
);

-- Always index by (client_id, created_at desc) for time-ordered queries
create index <table_name>_client_created on <table_name>(client_id, created_at desc);

-- Enable RLS
alter table <table_name> enable row level security;

-- Standard isolation policy
create policy tenant_isolation on <table_name>
  for all
  using (client_id = current_setting('app.current_client_id', true)::uuid);
```

**Common mistakes:**
- Forgetting the `client_id` column. Even if "this table is only ever accessed for one client at a time," add it. Future-you will thank you.
- Forgetting `on delete cascade` on the foreign key. When a client off-boards, you want their data to delete cleanly.
- Using `restrictive` policies. Don't. Use `permissive` (default) `tenant_isolation` policies only.

### Cross-tenant queries (background jobs, admin)

Some operations legitimately span tenants — nightly digest cron, admin dashboards, etc. In these cases:

```python
# Pattern: iterate tenants explicitly, set context per iteration
async def nightly_digest_job():
    async for client in fetch_active_clients():
        async with set_tenant_context(client.id) as session:
            digest = await build_digest(session)
            await send_digest(client, digest)
```

**Never** disable RLS bypass globally. **Never** use a "superuser" role for normal operations. The middleware-set context is the only legitimate way to scope queries.

## Webhook routing

### Path-based identification (preferred)

```python
@app.post("/webhooks/twilio/missed-call/{client_id}")
async def handle_missed_call(client_id: UUID, payload: TwilioPayload):
    # Tenant resolver middleware has already:
    # - Validated client_id exists and is active
    # - Verified webhook signature with client's secret
    # - Set app.current_client_id
    
    config = await ClientConfig.get_current()  # automatically scoped
    await process_missed_call(payload, config)
```

### Payload-based identification (fallback)

For webhook sources that don't support path parameters:

```python
@app.post("/webhooks/shopify")
async def handle_shopify_webhook(request: Request):
    payload = await request.json()
    shop_domain = payload.get("shop_domain")
    
    client = await Client.find_by_shopify_domain(shop_domain)  # admin context
    if not client:
        return JSONResponse({"error": "unknown shop"}, status_code=404)
    
    async with set_tenant_context(client.id):
        # Now all queries are scoped
        await process_shopify_event(payload)
```

### Webhook signing secrets

Every per-client webhook has a per-client signing secret stored in `client_configs.webhook_signing_secrets`:

```json
{
  "twilio": "whsec_abc123...",
  "shopify": "shpss_def456...",
  "ghl": "ghl_secret_xyz789..."
}
```

The tenant resolver verifies the signature using the appropriate secret. **Never** use a global signing secret across clients.

## Tenant resolver middleware

```python
from fastapi import Request, HTTPException
from contextvars import ContextVar

current_client_id: ContextVar[UUID | None] = ContextVar('current_client_id', default=None)

@app.middleware("http")
async def tenant_resolver(request: Request, call_next):
    # Extract client_id from path or payload
    client_id = await extract_client_id(request)
    
    if client_id:
        client = await Client.get(client_id, use_admin_context=True)
        if not client or client.status != 'active':
            raise HTTPException(status_code=404)
        
        # Verify signature for webhook routes
        if request.url.path.startswith('/webhooks/'):
            verify_webhook_signature(request, client)
        
        # Set both contexts: Python contextvar AND Postgres session var
        current_client_id.set(client_id)
        # Postgres session var is set via DB connection acquisition (see db.py)
    
    response = await call_next(request)
    return response
```

## The tenant isolation test suite

This runs in CI on every commit. Failure blocks merge.

```python
# tests/test_tenant_isolation.py
import pytest

@pytest.mark.parametrize("endpoint", ALL_TENANT_SCOPED_ENDPOINTS)
async def test_cannot_access_other_tenant_data(endpoint, client_a_token, client_b_data):
    """Client A's auth should never see Client B's data."""
    response = await authenticated_request(
        endpoint=endpoint,
        token=client_a_token,
        target_resource_id=client_b_data.id,  # belongs to client B
    )
    
    # Must be either 404 (not found, correctly scoped) or 403 (forbidden)
    # Must NOT be 200 with another tenant's data
    assert response.status_code in (404, 403), f"LEAK on {endpoint}"
    
    if response.status_code == 200:
        # If somehow 200, verify the data is NOT client B's
        assert response.json()["client_id"] != str(client_b_data.client_id)
```

Update this test file whenever you add a new endpoint or table.

## Audit logging

Every write operation logs `(client_id, actor, operation, target_id)`:

```sql
create table audit_log (
  id uuid primary key default gen_random_uuid(),
  client_id uuid references clients(id),
  actor text not null,                -- 'system' | 'founder' | client user email
  operation text not null,            -- 'create_lead', 'update_config', etc.
  target_table text,
  target_id uuid,
  changes jsonb,
  created_at timestamptz not null default now()
);
```

This is intentionally NOT under RLS — admin operations need to read across tenants. Lock down via separate auth.

## When to escalate a tenant to dedicated infrastructure

Default is shared. Promote to dedicated only when:

- Compliance requires it (HIPAA, etc. — rare for this niche)
- They're large enough that their query patterns degrade neighbors
- They're paying enterprise pricing ($2,500+/mo) to fund the overhead

The code stays identical — only the database connection string and deployment target change. Make this a config flag, not a fork.
