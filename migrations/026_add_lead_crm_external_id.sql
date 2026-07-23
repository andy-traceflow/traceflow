-- migrations/026_add_lead_crm_external_id.sql
--
-- Split leads.external_id's two conflated meanings.
--
-- external_id was documented as "the CRM record id after push" (migration 004),
-- but the ingestion webhooks seed it with the SOURCE-system id at creation:
-- Twilio CallSid (twilio_missed_call), Shopify order id, or a generic-webhook
-- field. Three CRM-id-keyed stages then misread that source id as a CRM id:
--   * the auto-push idempotency guard (webhooks/twilio.py) skipped the push for
--     every missed-call lead ("already has an external_id" was always true),
--   * admin re-push PATCHed the CRM at /contacts/{CallSid} (404, no contact),
--   * revenue_sync fetched recovered value with the CallSid -> silent mismatch,
--     so missed-call leads never got confirmed recovered-revenue attribution.
--
-- Give the CRM id its own column (mirrors contacts.crm_external_id, migration
-- 018): external_id keeps the source-system id, crm_external_id holds the CRM
-- record id, and pushed_to_crm_at stays the "has been pushed" flag.
--
-- Backfill: for leads that were genuinely pushed, external_id currently holds
-- the CRM id (the push wrote it there), so copy it over. pushed_to_crm_at IS NOT
-- NULL is the reliable "was actually pushed" signal (it is never set at lead
-- creation). external_id is left as-is on those rows; nothing reads it anymore.
--
-- HOW TO RUN:
--   python scripts/apply_migrations.py   (with SUPABASE_DB_URL set)
--   or paste into Supabase Dashboard > SQL Editor > Run

BEGIN;

ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS crm_external_id TEXT;

COMMENT ON COLUMN leads.crm_external_id IS
    'The lead''s record id in the client''s CRM, set on push. Distinct from '
    'external_id (the source-system id: Twilio CallSid, Shopify order id, etc). '
    'NULL until pushed. Mirrors contacts.crm_external_id.';

UPDATE leads
   SET crm_external_id = external_id
 WHERE pushed_to_crm_at IS NOT NULL
   AND crm_external_id IS NULL;

COMMIT;
