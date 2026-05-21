-- migrations/010_force_rls_on_tenant_tables.sql
--
-- CRITICAL FIX: enforce RLS even for the table owner role.
--
-- Background: PostgreSQL skips RLS for the role that owns a table
-- (and any superuser) by default. On Supabase, tables created via
-- a `postgres`-role connection are owned by `postgres` — which is
-- also the role the FastAPI service uses via SUPABASE_DB_URL.
-- Net effect: every RLS policy we wrote in migrations 001–009 was
-- being silently bypassed, leaking every tenant's data to every
-- other tenant.
--
-- This was caught by running tests/test_tenant_isolation.py against
-- the live database for the first time:
--   FAILED test_leads_are_tenant_isolated   (Client B saw Client A's lead)
--   FAILED test_kb_entries_are_tenant_isolated  (same on KB)
--   FAILED test_no_tenant_context_denies_all_reads  (no context, full visibility)
--
-- Fix: apply `FORCE ROW LEVEL SECURITY` to every tenant-scoped table.
-- This makes RLS policies apply to the owner too. The set_config()
-- machinery in src/app/db.py is now actually doing work.
--
-- NOT included (intentionally):
--   - audit_log         — written via SECURITY DEFINER trigger; reads
--                         use service role for cross-tenant lookups.
--                         FORCE would break the trigger insert path.
--   - schema_migrations — owned by the migration runner; FORCE would
--                         deny the runner's own bookkeeping inserts.
--
-- HOW TO RUN:
--   python scripts/apply_migrations.py     (with SUPABASE_DB_URL set)

BEGIN;

ALTER TABLE clients                 FORCE ROW LEVEL SECURITY;
ALTER TABLE client_configs          FORCE ROW LEVEL SECURITY;
ALTER TABLE client_field_mappings   FORCE ROW LEVEL SECURITY;
ALTER TABLE client_webhook_configs  FORCE ROW LEVEL SECURITY;
ALTER TABLE leads                   FORCE ROW LEVEL SECURITY;
ALTER TABLE messages                FORCE ROW LEVEL SECURITY;
ALTER TABLE events                  FORCE ROW LEVEL SECURITY;
ALTER TABLE kb_entries              FORCE ROW LEVEL SECURITY;
ALTER TABLE kb_documents            FORCE ROW LEVEL SECURITY;
ALTER TABLE kb_chunks               FORCE ROW LEVEL SECURITY;
ALTER TABLE sync_log                FORCE ROW LEVEL SECURITY;
ALTER TABLE product_yields          FORCE ROW LEVEL SECURITY;
ALTER TABLE calculator_configs      FORCE ROW LEVEL SECURITY;
ALTER TABLE user_permissions        FORCE ROW LEVEL SECURITY;

COMMIT;
