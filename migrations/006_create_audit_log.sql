-- migrations/006_create_audit_log.sql
--
-- Defense-in-depth audit log. Every write operation on tenant-scoped
-- tables logs (client_id, actor, operation, target). Read access is
-- intentionally NOT under RLS — admin operations need to read across
-- tenants. Locked down via separate auth (admin JWT).
--
-- Generic trigger function attaches to any tenant-scoped table that
-- wants automatic logging. Apply via:
--
--   CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON <table>
--       FOR EACH ROW EXECUTE FUNCTION log_audit_change();
--
-- HOW TO RUN:
--   1. Open Supabase Dashboard > SQL Editor > New query
--   2. Paste this entire file
--   3. Click Run

BEGIN;

CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id       UUID,                                                  -- nullable for system-level events
    actor           TEXT         NOT NULL DEFAULT 'system',                -- 'system' | 'founder' | client user email
    actor_user_id   UUID,                                                  -- nullable; populated when auth.uid() is available
    operation       TEXT         NOT NULL CHECK (operation IN ('create', 'update', 'delete', 'login', 'export', 'sync')),
    target_table    TEXT,
    target_id       TEXT,                                                  -- TEXT not UUID — some tables use BIGINT PKs
    snapshot        JSONB,                                                 -- NEW state for create/update, OLD for delete
    changed_fields  TEXT[],                                                -- update-only
    occurred_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_client_time
    ON audit_log(client_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_log_target
    ON audit_log(target_table, target_id, occurred_at DESC);

-- ---------------------------------------------------------------------------
-- log_audit_change(): generic trigger handler
--
-- Captures the diff between OLD and NEW (excluding updated_at since the
-- set_updated_at trigger always bumps it). Pulls client_id from the row
-- when the table has that column.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION log_audit_change()
RETURNS TRIGGER AS $$
DECLARE
    diff TEXT[];
    row_client_id UUID;
    op TEXT;
    target_id_value TEXT;
    snapshot_value JSONB;
BEGIN
    IF TG_OP = 'INSERT' THEN
        op := 'create';
        snapshot_value := to_jsonb(NEW);
        target_id_value := (to_jsonb(NEW)->>'id');
        row_client_id := NULLIF(to_jsonb(NEW)->>'client_id', '')::uuid;
        diff := NULL;

    ELSIF TG_OP = 'UPDATE' THEN
        op := 'update';
        SELECT ARRAY(
            SELECT key
            FROM jsonb_each(to_jsonb(NEW)) AS new_kv(key, val)
            WHERE val IS DISTINCT FROM (to_jsonb(OLD)->key)
              AND key NOT IN ('updated_at')
        ) INTO diff;

        IF diff IS NULL OR array_length(diff, 1) IS NULL THEN
            RETURN NEW;   -- no real change; skip log
        END IF;

        snapshot_value := to_jsonb(NEW);
        target_id_value := (to_jsonb(NEW)->>'id');
        row_client_id := NULLIF(to_jsonb(NEW)->>'client_id', '')::uuid;

    ELSIF TG_OP = 'DELETE' THEN
        op := 'delete';
        snapshot_value := to_jsonb(OLD);
        target_id_value := (to_jsonb(OLD)->>'id');
        row_client_id := NULLIF(to_jsonb(OLD)->>'client_id', '')::uuid;
        diff := NULL;
    END IF;

    INSERT INTO audit_log (client_id, actor_user_id, operation, target_table, target_id, snapshot, changed_fields)
    VALUES (
        row_client_id,
        auth.uid(),
        op,
        TG_TABLE_NAME,
        target_id_value,
        snapshot_value,
        diff
    );

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Wire trigger onto canonical tables. Add more as new tables come online.
CREATE TRIGGER trg_audit_leads
    AFTER INSERT OR UPDATE OR DELETE ON leads
    FOR EACH ROW EXECUTE FUNCTION log_audit_change();

CREATE TRIGGER trg_audit_kb_entries
    AFTER INSERT OR UPDATE OR DELETE ON kb_entries
    FOR EACH ROW EXECUTE FUNCTION log_audit_change();

CREATE TRIGGER trg_audit_client_configs
    AFTER INSERT OR UPDATE OR DELETE ON client_configs
    FOR EACH ROW EXECUTE FUNCTION log_audit_change();

-- ---------------------------------------------------------------------------
-- RLS on audit_log: read-locked via separate admin auth, not the per-request
-- tenant context. We DO NOT enable a tenant_isolation policy here because
-- founder-level reads (cross-tenant troubleshooting) need to bypass it.
-- Reads must use the service role; writes happen via SECURITY DEFINER trigger.
-- ---------------------------------------------------------------------------
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
-- No policy created → only service_role can read/write (RLS denies by default).

COMMIT;
