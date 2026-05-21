-- migrations/011_null_safe_tenant_policies.sql
--
-- Make tenant_isolation policies NULL-safe.
--
-- Background: the original policies cast `current_setting(...)` directly
-- to UUID:
--     USING (client_id = current_setting('app.current_client_id', true)::uuid)
--
-- When the setting was unset, `current_setting(..., true)` returns NULL,
-- the cast is fine, and `client_id = NULL` is NULL → treated as false →
-- row excluded. So far so good.
--
-- BUT: when the setting is explicitly set to empty string '' (which
-- happens whenever code clears the tenant context via
-- `set_config('app.current_client_id', '', ...)` instead of resetting
-- it), the cast `''::uuid` raises:
--     ERROR: invalid input syntax for type uuid: ""
--
-- That's the "deny all reads when no tenant context" path crashing
-- rather than gracefully returning zero rows. Caught by
-- tests/test_tenant_isolation.py::test_no_tenant_context_denies_all_reads.
--
-- Fix: wrap the setting lookup in NULLIF(..., '') so empty string
-- collapses to NULL before the cast. `NULL::uuid` is NULL, comparison
-- is NULL, row is silently excluded. No crash, no leak.
--
-- HOW TO RUN:
--   python scripts/apply_migrations.py   (with SUPABASE_DB_URL set)

BEGIN;

-- ---------------------------------------------------------------------------
-- Helper: rewrite the "FOR ALL" tenant_isolation policy on a table that
-- filters on a column named `client_id`.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    t TEXT;
    tables_with_client_id TEXT[] := ARRAY[
        'client_configs',
        'client_field_mappings',
        'client_webhook_configs',
        'leads',
        'messages',
        'events',
        'kb_entries',
        'kb_documents',
        'kb_chunks',
        'sync_log',
        'product_yields',
        'calculator_configs'
    ];
BEGIN
    FOREACH t IN ARRAY tables_with_client_id
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I '
            'FOR ALL '
            'USING (client_id = NULLIF(current_setting(''app.current_client_id'', true), '''')::uuid)',
            t
        );
    END LOOP;
END
$$;

-- ---------------------------------------------------------------------------
-- `clients` is special — its tenant-key column is `id`, not `client_id`.
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS tenant_isolation ON clients;
CREATE POLICY tenant_isolation ON clients
    FOR ALL
    USING (id = NULLIF(current_setting('app.current_client_id', true), '')::uuid);

-- ---------------------------------------------------------------------------
-- `user_permissions` has three separate policies for INSERT/UPDATE/DELETE
-- that share the same pattern. The fourth (`users_read_own`) is keyed off
-- `auth.uid()` and is unaffected.
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS tenant_isolation_write ON user_permissions;
CREATE POLICY tenant_isolation_write ON user_permissions
    FOR INSERT TO authenticated
    WITH CHECK (client_id = NULLIF(current_setting('app.current_client_id', true), '')::uuid);

DROP POLICY IF EXISTS tenant_isolation_update ON user_permissions;
CREATE POLICY tenant_isolation_update ON user_permissions
    FOR UPDATE TO authenticated
    USING (client_id = NULLIF(current_setting('app.current_client_id', true), '')::uuid);

DROP POLICY IF EXISTS tenant_isolation_delete ON user_permissions;
CREATE POLICY tenant_isolation_delete ON user_permissions
    FOR DELETE TO authenticated
    USING (client_id = NULLIF(current_setting('app.current_client_id', true), '')::uuid);

COMMIT;
