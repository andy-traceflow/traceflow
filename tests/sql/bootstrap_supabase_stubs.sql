-- tests/sql/bootstrap_supabase_stubs.sql
--
-- CI-only: stub the parts of Supabase's `auth` schema and the standard
-- Supabase roles (`anon`, `authenticated`, `service_role`) that our
-- migrations and runtime code depend on, so everything applies cleanly
-- against a vanilla Postgres + pgvector image without needing a live
-- Supabase project.
--
-- Run this BEFORE the migrations in migrations/. The CI workflow
-- (.github/workflows/ci.yml) does that. Do NOT run this against
-- production — Supabase already provides all of this.

-- ---------------------------------------------------------------------------
-- auth schema + auth.users + auth.uid()
-- ---------------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS auth;

-- Minimum shape: migration 008 has `REFERENCES auth.users(id) ON DELETE CASCADE`.
CREATE TABLE IF NOT EXISTS auth.users (
    id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT
);

-- Supabase's auth.uid() reads the JWT's `sub` claim from the request
-- context. For tests we read from a session-local setting so test code
-- can simulate "the request is being made by user X" via
-- `SELECT set_config('app.current_user_id', '<uuid>', true)`.
CREATE OR REPLACE FUNCTION auth.uid()
RETURNS UUID
LANGUAGE SQL
STABLE
AS $$
    SELECT NULLIF(current_setting('app.current_user_id', true), '')::UUID;
$$;

-- ---------------------------------------------------------------------------
-- Supabase's standard roles
--
-- Migration 008 creates policies `TO authenticated`, which fails if that
-- role doesn't exist. Production `src/app/db.py` runs `SET ROLE
-- authenticated` per request so RLS actually applies (the default
-- `postgres` role has bypassrls=true on Supabase). The tenant isolation
-- test suite does the same.
--
-- Attributes mirror Supabase's managed project defaults so CI behaves
-- like prod:
--   - anon, authenticated: NOLOGIN, no bypassrls (RLS enforced)
--   - service_role: NOLOGIN, BYPASSRLS (admin/backend access)
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN NOINHERIT NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN NOINHERIT NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS;
    END IF;
END
$$;

-- Schema usage
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT USAGE ON SCHEMA auth   TO authenticated, service_role;

-- Default privileges: any tables created AFTER this point (i.e. by the
-- migrations that run next in CI) automatically get DML grants for these
-- roles. Supabase managed projects set up the same pattern.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO anon, authenticated, service_role;

-- Existing tables (just auth.users at this point in the bootstrap)
GRANT SELECT ON ALL TABLES IN SCHEMA auth TO authenticated, service_role;
