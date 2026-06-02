-- migrations/012_add_is_test_to_leads.sql
--
-- Test-lead flag for admin tooling. Set via the Retool admin UI
-- ("Mark as test"); lets dashboards filter out leads created during
-- system testing or onboarding shakedowns without deleting them.
--
-- HOW TO RUN:
--   1. Open Supabase Dashboard > SQL Editor > New query
--   2. Paste this entire file
--   3. Click Run

BEGIN;

ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE;

-- Partial index — most leads are not test leads, so this stays small.
CREATE INDEX IF NOT EXISTS idx_leads_client_is_test
    ON leads(client_id) WHERE is_test = true;

COMMIT;
