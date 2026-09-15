-- migrations/023_add_business_profile.sql
--
-- Structured home for onboarding fields that have no first-class column today.
--
-- The onboarding form (Jotform now, native later) collects business identity
-- and logistics data — street address, DBA, website, Google Business Profile,
-- structured owner/point-of-contact people, tech inventory, and support
-- logistics — that today would only survive as ad-hoc keys inside `brand` or
-- the alert arrays. Rather than sprinkle ~15 sparse typed columns onto
-- client_configs, we add ONE JSONB block, mirroring the existing `brand` /
-- `revenue_config` pattern: code reads it through accessors on ClientConfig
-- with defaults baked in, so a partial/empty object still behaves.
--
-- Shape (all optional; the promote mapper populates what the form provided):
--   {
--     "legal_name": "...", "dba": "...",
--     "address": "...", "website_url": "...",
--     "google_business_profile_url": "...",
--     "google_business_profile_owner_email": "...",
--     "owner":       {"name": "...", "phone": "...", "email": "..."},
--     "day_to_day":  {"name": "...", "phone": "...", "email": "..."},
--     "tech":        {"voip": "...", "cms": "...", "domain_registrar": "...",
--                     "ecommerce": "...", "other": "..."},
--     "logistics":   {"support_channel": "...", "ooo": "...", "cc_emails": [...]}
--   }
--
-- NOTE: credentials are never stored here. Secrets stay in crm_credentials /
-- webhook_signing_secrets, collected out-of-band. The form and the promote
-- mapper both preserve that boundary.
--
-- HOW TO RUN:
--   python scripts/apply_migrations.py   (with SUPABASE_DB_URL set)
--   or paste into Supabase Dashboard > SQL Editor > Run

BEGIN;

ALTER TABLE client_configs
    ADD COLUMN IF NOT EXISTS business_profile JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN client_configs.business_profile IS
    'Structured business identity + logistics captured at onboarding '
    '(address, DBA, website, GBP, owner/PoC contacts, tech inventory, support '
    'logistics). Read via ClientConfig accessors. Never holds credentials.';

COMMIT;
