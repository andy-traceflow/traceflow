-- migrations/003_create_client_webhook_configs.sql
--
-- Layer 3 of the three-layer integration model: the escape hatch for
-- long-tail systems where building a full adapter isn't justified
-- (homegrown CRMs, niche field-service tools, etc.). Each row stores
-- per-client parsing rules (JSONPath / jq / templates) so the generic
-- webhook handler can extract canonical fields from any payload shape.
--
-- See app/webhooks/generic.py for the runtime side.
--
-- HOW TO RUN:
--   1. Open Supabase Dashboard > SQL Editor > New query
--   2. Paste this entire file
--   3. Click Run

BEGIN;

CREATE TABLE IF NOT EXISTS client_webhook_configs (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id         UUID         NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    webhook_slug      TEXT         NOT NULL,                 -- e.g. 'jobber-lead-created'
    parser_type       TEXT         NOT NULL
                                   CHECK (parser_type IN ('jsonpath', 'jq', 'python_template')),
    field_extractors  JSONB        NOT NULL,                 -- {canonical_field: extraction_expression}
    signing_secret    TEXT,                                  -- HMAC-SHA256 secret; null = unsigned (dev only)
    signing_algorithm TEXT         NOT NULL DEFAULT 'hmac_sha256'
                                   CHECK (signing_algorithm IN ('hmac_sha256', 'hmac_sha256_timestamped', 'none')),
    signature_header  TEXT,                                  -- header name carrying the signature
    description       TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (client_id, webhook_slug)
);

CREATE INDEX IF NOT EXISTS idx_webhook_configs_lookup
    ON client_webhook_configs(client_id, webhook_slug);

CREATE TRIGGER trg_client_webhook_configs_updated_at BEFORE UPDATE ON client_webhook_configs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE client_webhook_configs ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON client_webhook_configs
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid);

COMMIT;
