-- migrations/013_add_classification_config_to_client_configs.sql
--
-- Caller-classification config for the lead lifecycle. Adds per-tenant
-- tolerances so the platform can distinguish genuine leads from existing
-- customers, vendors, and spam BEFORE spending SMS/AI. Configuration over
-- customization: a client who wants everyone texted and a client who wants
-- tight filtering both live in config rows, not code branches.
-- See docs/workflow-schema.md Section 3 (Technical Lead Lifecycle).
--
-- HOW TO RUN:
--   1. Open Supabase Dashboard > SQL Editor > New query
--   2. Paste this entire file
--   3. Click Run

BEGIN;

-- Per-client classification tolerances (nested JSONB, mirrored by the
-- convenience accessors on app.models.client_config.ClientConfig).
ALTER TABLE client_configs
    ADD COLUMN IF NOT EXISTS classification_config JSONB NOT NULL DEFAULT '{
        "crm_lookup_enabled": true,
        "spam_filtering_enabled": true,
        "spam_risk_threshold": "moderate",
        "text_existing_customers": true,
        "text_vendors": false,
        "drop_spam_silently": true
    }'::jsonb;

-- Who gets pinged when an existing customer hits voicemail. An existing
-- customer reaching voicemail is a priority service event, ranked above a
-- cold lead. NULL falls back to the owner_alert_* contacts.
ALTER TABLE client_configs
    ADD COLUMN IF NOT EXISTS existing_customer_alert_contact TEXT;

-- Phase 1+: explicit vendor/partner numbers to never sales-text. NOT NULL
-- DEFAULT '{}' matches the other array columns on this table so the Pydantic
-- model never has to handle a NULL list.
ALTER TABLE client_configs
    ADD COLUMN IF NOT EXISTS vendor_allowlist TEXT[] NOT NULL DEFAULT '{}';

COMMIT;
