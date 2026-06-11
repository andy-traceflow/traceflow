# ADR-0004: Self-hosted admin surface replaces Retool

**Date:** 2026-06-10
**Status:** accepted

## Context

The roadmap (PRD UI maturity model, architecture.md deferral list) planned the founder admin tool as Retool at Phase 2. Meanwhile the operating reality is Phase 0/1: client config edits and lead inspection happen in raw SQL via Supabase Studio, which is slow, audit-less, and one fat-fingered UPDATE away from a tenant-isolation incident. The lead-lifecycle v2 work (classification, intent routing, per-client filtering config) made the config surface meaningfully larger, and the recovered-revenue work (ADR-0003) added an owner-report flow that needs a UI. A prior session's retool-notes.md already documented Retool's friction (connector setup, silent query failures, per-panel SQL duplication).

## Decision

Build a minimal self-hosted admin surface inside the existing FastAPI app: `/api/admin/*` endpoints plus a thin React SPA served at `/admin` from the same service.

- **Identity:** an `admin_users` table (migration 017) with bcrypt password hashes — a table, not an env-var password, so future partners/employees are an INSERT (+ role-CHECK widening), not a rewrite. Seed/reset via `scripts/create_admin.py`; the UI never writes this table.
- **Auth:** `POST /api/admin/login` (rate-limited 5 failures/15min/IP, timing-equalized, enumeration-safe 401s) issues a 12-hour HS256 JWT signed with the existing `ADMIN_JWT_SECRET`. Every other admin route hangs off `require_admin_user` (services/admin_auth.py), declared as a router-level dependency per submodule. The gate re-loads the admin row per request and checks `is_active` — deactivation is instant revocation, stronger than a jti denylist. The old static-secret bearer (`verify_admin_token`) is retired: every admin action now has a named actor.
- **Isolation:** admin work is cross-tenant by design, so endpoints use the service-role connection (BYPASSRLS). The invariant replacing RLS here: **every SQL statement filters by the `client_id` from the URL path**; lead lookups are `WHERE id = $1 AND client_id = $2` and a cross-client id is an indistinguishable 404. A gate-sweep test iterates every `/api/admin` route asserting 401 without a token, so future routes are covered automatically.
- **Audit:** every write records `audit_log` with `actor = admin email`, `actor_user_id = admin id`, mapped onto the existing operation CHECK (`update`/`delete`/`sync`/`login`).
- **Secrets stay out:** `crm_credentials` / `webhook_signing_secrets` are surfaced read-only as presence/keys (`has_crm_credentials`, `webhook_integrations`) and are structurally unwritable (`extra="forbid"` update model). Provisioning and client `status` changes remain the onboarding script's job.
- **SPA:** Vite + React + Tailwind in `admin-ui/`, built bundle committed to `src/app/static/admin/` and mounted by main.py (same origin → zero CORS surface; Render build stays pip-only). Token in sessionStorage; any 401 bounces to login. `scripts/dev_admin_preview.py` runs the real app over canned in-memory data for UI work without a database.
- **bcrypt directly, not passlib** (spec deviation): passlib (last release 2020) crashes importing bcrypt≥4.1; direct use is two functions. bcrypt truncates at 72 bytes — create_admin.py enforces 8–72-byte passwords. `checkpw` blocks the loop ~250ms, acceptable on a rate-limited login only.

## Alternatives considered

- **Retool (status quo plan):** fastest to first panel, but retool-notes.md already logged connector friction and silently-failing queries; per-panel SQL duplicates the isolation discipline with no test coverage; $10+/user/mo; and the admin data path would live outside the repo's CI. Rejected.
- **Keep static-secret auth + Supabase Studio:** no identity for audit (`actor='founder_retool'` hardcoded), no rate limiting, and SQL-by-hand against production. Rejected.
- **Supabase Auth for admins:** conflates platform-admin identity with the Phase-3 client-portal user pool (`middleware/auth.py` Supabase path) and adds an external dependency to the most security-sensitive surface. Rejected.

## Consequences

### Positive
- Config edits and lead/routing visibility without SQL; every write attributable and audit-logged; the metrics-integrity view (routing breakdown) keeps the 25%+ recovery guarantee auditable.
- The gate, isolation filters, and partial-update semantics are all under the offline test suite (55+ admin tests), unlike any Retool panel.
- Multi-admin ready: RBAC is a role-CHECK widening + per-route role checks later.

### Negative
- ~18 routes + an SPA to maintain in-repo (mitigated: the SPA is deliberately a tool, not a product).
- In-memory rate limiter is per-process — fine single-instance; needs a shared store if the service scales horizontally.
- Committed build artifacts in `src/app/static/admin/` add diff noise on SPA changes.
- **Breaking change at deploy:** bare-`ADMIN_JWT_SECRET` bearer tokens stop working; the founder must run migration 017 + `create_admin.py`, then log in.

### Reversibility
Moderate. The API is additive; dropping the SPA is deleting a directory and a mount. Going back to Retool would only require pointing panels at the same endpoints (better than raw SQL even then).

## Implementation notes

- Code: migration 017; `services/admin_auth.py`; `routers/admin/` package (auth, clients, leads, activity, mappings, schemas); `scripts/create_admin.py`; `scripts/dev_admin_preview.py`; `admin-ui/`; main.py static mount; `verify_admin_token` deleted from middleware/auth.py.
- Tests: `tests/services/test_admin_auth.py`, `tests/test_admin.py` (gate sweep, login matrix, isolation 404s, partial update, redaction, parity ports of repush/outcome).
- Deploy runbook: merge → `python scripts/apply_migrations.py` (017) → `python scripts/create_admin.py --email andy@traceflow.app --name "Andy"` against the prod DSN → confirm `ADMIN_JWT_SECRET` (≥32 random bytes) in Render → admin at `https://<api-host>/admin`.
- Supersedes docs/retool-notes.md (banner added; kept for history — its client-switcher + explicit-client_id conventions carried into this design).
