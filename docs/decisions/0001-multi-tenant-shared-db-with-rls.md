# ADR-0001: Single shared Supabase project with RLS-based tenant isolation

**Date:** 2026-05-13
**Status:** accepted

## Context

TraceFlow is a multi-tenant SaaS platform for SMB surface/countertop/flooring contractors. We must decide how to isolate tenants in the database layer. This decision has cascading implications for operational complexity, cost, security, and the scaling ceiling of the business.

Three primary models exist:

1. **Single shared project, schema-based isolation** — one Postgres database, one schema, every table has `client_id`, isolation enforced by application code
2. **Single shared project, RLS-based isolation** — one Postgres database, one schema, every table has `client_id`, isolation enforced by Postgres Row Level Security policies (chosen)
3. **Project-per-tenant** — each client gets a dedicated Supabase project, isolated by infrastructure

The business is pre-launch (Phase 0). Target by Month 6 is 5 clients; by Month 12, 8-15 clients. Strategic fork at Month 12 may push to 50+ clients (Path 2 SaaS) or stay at 15-25 (Path 1 lifestyle).

## Decision

We will use **single shared Supabase project with RLS-based tenant isolation**.

- All tenants share one Supabase project for as long as practical (target: through Year 1, likely longer)
- Every tenant-scoped table has a `client_id` column and an RLS policy filtering by `current_setting('app.current_client_id')`
- Tenant resolver middleware in FastAPI sets the Postgres session variable on every request
- Tenant isolation tests run in CI on every commit, asserting no cross-tenant data leakage

## Alternatives considered

### Option 1: Schema-based isolation (no RLS)
- **Pros:** Simpler mental model; no RLS gotchas; existing SEMCO code is closer to this
- **Cons:** Single bug in application code (forgotten `where client_id = X` clause) leaks data. No defense in depth. **Rejected** because the cost of a leak is catastrophic and developers (especially future contractors/VAs) will eventually forget the filter.

### Option 3: Project-per-tenant
- **Pros:** Maximum isolation; cleaner off-boarding (delete the project); easier compliance posture if it ever matters (HIPAA, etc.)
- **Cons:** 
  - Cost: $25/mo × N clients = $250+/mo at 10 clients, vs $25 flat for shared
  - Operational complexity: schema migrations must run against N projects; monitoring N projects; deploying N services
  - Slower client onboarding: provisioning a new Supabase project takes minutes-to-hours, not seconds
  - Worse cross-tenant analytics (have to aggregate across many DBs)
- **Rejected** as a default. Will be available as an **escape hatch** for enterprise clients ($2,500+/mo tier) who need it or for clients with genuine compliance demands.

## Consequences

### Positive

- **Single migration target.** Schema changes deploy to one place.
- **Cheap to operate.** $25-50/mo Supabase covers 10+ clients comfortably.
- **Fast onboarding.** Provisioning a new tenant is inserting rows, not creating infrastructure.
- **Easy cross-tenant analytics.** Founder dashboard, monitoring, billing reports all read from one DB.
- **Defense in depth.** RLS catches application-layer bugs that schema-based isolation wouldn't.
- **Code stays uniform.** All adapters, services, and webhooks share the same code paths.

### Negative

- **RLS adds debugging complexity.** "Why is this query returning empty?" sometimes means "you forgot to set the tenant context." Mitigated by clear middleware patterns and dev tooling.
- **One catastrophic Postgres incident affects all clients.** Mitigated by Supabase's managed backups + nightly logical backups to our own S3.
- **Single-region constraint until project-per-tenant.** Latency for non-US clients may degrade. Not a Year-1 concern.
- **Forced discipline on schema design.** Every tenant-scoped table needs `client_id` from the start. Mitigated by linting + the tenant isolation test suite.

### Reversibility

Reversing this decision (moving a specific client to a dedicated project) is **moderately easy** and is in fact the planned escape hatch:
- Code changes: zero (config flag for database connection string per client)
- Data migration: 1-2 hours per client for a logical dump + restore + RLS removal
- Cost: $25/mo per migrated client

We can promote individual clients to dedicated projects on demand without rearchitecting.

Reversing the **opposite** direction (collapsing project-per-tenant into shared) would be much harder, which is part of why we're starting with shared.

## Implementation notes

See:
- `docs/architecture.md` § Multi-tenancy model
- `.claude/skills/multi-tenant-arch/SKILL.md`
- `tests/test_tenant_isolation.py` (to be created)

## Decision review trigger

Revisit this decision if any of these occur:

- Total client count exceeds 30
- A single client's query patterns degrade neighbors
- A client signs an enterprise contract with explicit isolation requirements
- A compliance regime (HIPAA, GDPR for EU clients) becomes relevant
- A cross-tenant data leak occurs in production (would force immediate redesign)
