-- migrations/007_create_sync_log.sql
--
-- Tracks bulk operations triggered against external systems (CRM pushes,
-- KB exports, knowledge base re-syncs, etc.). Per-tenant for visibility
-- in client portals; cross-tenant aggregated views use the service role.
--
-- HOW TO RUN:
--   1. Open Supabase Dashboard > SQL Editor > New query
--   2. Paste this entire file
--   3. Click Run

BEGIN;

CREATE TABLE IF NOT EXISTS sync_log (
    id               BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id        UUID         NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    target           TEXT         NOT NULL,                                -- 'crm' | 'kb_export' | 'kb_ingest' | etc.
    started_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    total_entries    INTEGER      NOT NULL DEFAULT 0,
    succeeded        INTEGER      NOT NULL DEFAULT 0,
    failed           INTEGER      NOT NULL DEFAULT 0,
    triggered_by     TEXT,                                                 -- email of authenticated user, or 'cron'
    error_summary    TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_log_client_target_started
    ON sync_log(client_id, target, started_at DESC);

ALTER TABLE sync_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON sync_log
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid);

COMMIT;
