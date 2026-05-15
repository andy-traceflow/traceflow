-- migrations/002_create_client_field_mappings.sql
--
-- Layer 2 of the three-layer integration model: per-client translation
-- from canonical field names to whatever the external system calls them.
-- Two clients on the same CRM can have wildly different custom-field
-- schemas; mappings live in data, not code.
--
-- Adapters in app/adapters/*.py consult this table at runtime via
-- app.services.field_mappings.resolve_mappings(). Field names are never
-- hardcoded.
--
-- HOW TO RUN:
--   1. Open Supabase Dashboard > SQL Editor > New query
--   2. Paste this entire file
--   3. Click Run

BEGIN;

CREATE TABLE IF NOT EXISTS client_field_mappings (
    client_id             UUID         NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    integration           TEXT         NOT NULL,           -- 'crm' | 'shopify' | 'website_form' | 'monday' | etc.
    canonical_field       TEXT         NOT NULL,           -- 'sqft' | 'service_type' | 'phone' | etc.
    external_field        TEXT         NOT NULL,           -- whatever the external system calls it
    external_field_type   TEXT         NOT NULL DEFAULT 'standard'
                                       CHECK (external_field_type IN ('standard', 'custom_field', 'custom_property', 'column')),
    transform             JSONB,                           -- optional value translation rules; see services/field_mappings.py
    notes                 TEXT,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, integration, canonical_field)
);

CREATE INDEX IF NOT EXISTS idx_field_mappings_lookup
    ON client_field_mappings(client_id, integration);

CREATE TRIGGER trg_client_field_mappings_updated_at BEFORE UPDATE ON client_field_mappings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE client_field_mappings ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON client_field_mappings
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid);

COMMIT;
