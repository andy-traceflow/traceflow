-- tests/sql/bootstrap_supabase_stubs.sql
--
-- CI-only: stub the parts of Supabase's `auth` schema that our
-- migrations depend on, so the migration files apply cleanly against
-- a vanilla Postgres + pgvector image without needing a live Supabase
-- project.
--
-- Run this BEFORE the migrations in migrations/. The CI workflow
-- (.github/workflows/ci.yml) does that. Do NOT run this against
-- production — Supabase already provides a real auth.users table.

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
