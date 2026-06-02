-- migrations/015_add_intent_statuses_to_leads.sql
--
-- Post-reply intent classification (intent_classification, Section 3 of the
-- workflow schema) runs on a lead's FIRST inbound SMS and can route the
-- conversation off the sales track entirely:
--   * existing_customer  -> qualification_status = 'support_touch'
--   * non_lead           -> qualification_status = 'non_lead_contact'
-- Both are terminal: the qualifier never runs, the owner sees the touch in
-- the records/digest, and the row falls out of the active qualification
-- window (qualification_status IN ('unqualified','qualifying')).
--
-- This widens the leads_qualification_status_check constraint to admit the
-- two new terminal statuses. The constraint was created inline (auto-named
-- leads_qualification_status_check) in migration 004, so we drop and recreate
-- it. No data is rewritten — existing rows already satisfy the wider set.
--
-- See docs/workflow-schema.md Section 3 (intent_classification branches).
--
-- HOW TO RUN:
--   1. Open Supabase Dashboard > SQL Editor > New query
--   2. Paste this entire file
--   3. Click Run

BEGIN;

ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_qualification_status_check;

ALTER TABLE leads ADD CONSTRAINT leads_qualification_status_check
    CHECK (qualification_status IN (
        'unqualified',
        'qualifying',
        'qualified',
        'high_value',
        'needs_review',
        'spam',
        'duplicate',
        'support_touch',
        'non_lead_contact'
    ));

COMMIT;
