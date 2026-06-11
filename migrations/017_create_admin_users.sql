-- migrations/017_create_admin_users.sql
--
-- Platform admin accounts for the self-hosted /api/admin surface (ADR-0004,
-- replaces the planned Retool tool). Single founder row today; the table
-- (rather than an env-var password) is the multi-admin hook — partners or
-- employees later are an INSERT plus a role-CHECK widening, not a rewrite.
--
-- Auth flow: POST /api/admin/login verifies bcrypt against password_hash and
-- issues a 12h HS256 JWT signed with ADMIN_JWT_SECRET; every admin request
-- re-loads this row and checks is_active (deactivation = instant revocation).
-- Seed/reset via scripts/create_admin.py — the UI never writes this table.
--
-- NOT tenant-scoped: admin accounts span clients by design, so there is no
-- client_id column and no tenant policy. RLS is ENABLE + FORCE with NO
-- policies — deny-all for the `authenticated` role (the tenant-scoped
-- connection path); only the service-role connection (BYPASSRLS, db.py
-- get_service_connection) can read or write it.
--
-- HOW TO RUN:
--   1. Open Supabase Dashboard > SQL Editor > New query
--   2. Paste this entire file
--   3. Click Run
-- (or: python scripts/apply_migrations.py)

BEGIN;

CREATE TABLE IF NOT EXISTS admin_users (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT         UNIQUE NOT NULL,        -- stored lowercased; app normalizes on login + seed
    password_hash   TEXT         NOT NULL,               -- bcrypt ($2b$, cost 12)
    name            TEXT         NOT NULL DEFAULT '',
    role            TEXT         NOT NULL DEFAULT 'owner'
                                  CHECK (role IN ('owner')),  -- widen to ('owner','partner','employee') when RBAC lands
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ
);

ALTER TABLE admin_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_users FORCE ROW LEVEL SECURITY;
-- No policies on purpose: deny-all under `authenticated`; service role only.

COMMIT;
