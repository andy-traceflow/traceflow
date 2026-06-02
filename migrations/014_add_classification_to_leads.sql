-- migrations/014_add_classification_to_leads.sql
--
-- Persistent disposition tag for the lead lifecycle. Where
-- qualification_status tracks how far a lead got through qualification,
-- classification records WHAT the caller is: a genuine recoverable lead,
-- an existing customer, a known non-lead (vendor/partner), or spam.
--
-- Set pre-send by caller_classification (Section 3) and refined post-reply
-- by intent_classification. Recovery-rate metrics are computed over
-- classification = 'potential_lead' ONLY — existing customers, vendors,
-- and spam never count against the missed-call recovery denominator.
--
-- DEFAULT 'potential_lead' backfills every pre-existing lead row, which is
-- correct: before this revision every missed call WAS treated as a lead.
-- See docs/workflow-schema.md Section 3 (Technical Lead Lifecycle).
--
-- HOW TO RUN:
--   1. Open Supabase Dashboard > SQL Editor > New query
--   2. Paste this entire file
--   3. Click Run

BEGIN;

ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS classification TEXT NOT NULL DEFAULT 'potential_lead'
        CHECK (classification IN ('potential_lead', 'existing_customer', 'known_non_lead', 'spam'));

-- Recovery-rate and digest queries filter leads by (client_id, classification).
CREATE INDEX IF NOT EXISTS idx_leads_client_classification
    ON leads(client_id, classification);

COMMIT;
