-- migrations/016_add_outcome_and_revenue_config.sql
--
-- Recovered-revenue attribution. TraceFlow goes blind the moment a lead is
-- pushed to the CRM — the quote/job/payment happen offline — so we need a
-- feedback channel to pull the booked value back and attribute it to the
-- missed call we recovered.
--
-- The `outcome` axis is orthogonal to both qualification_status (how far the
-- lead got) and classification (what the caller is): it records whether the
-- recovered lead ultimately BOOKED, and for how much. recovered_value is the
-- actual dollars; outcome_source records WHERE the number came from so actuals
-- (crm / owner_report) are never silently blended with the budget-bucket
-- `estimated` proxy the digest shows.
--
-- DEFAULT outcome='open' backfills every existing lead correctly: nothing has
-- been confirmed booked yet. See docs/decisions/0003-recovered-revenue-attribution.md.
--
-- HOW TO RUN:
--   1. Open Supabase Dashboard > SQL Editor > New query
--   2. Paste this entire file
--   3. Click Run

BEGIN;

ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS outcome TEXT NOT NULL DEFAULT 'open'
        CHECK (outcome IN ('open', 'won', 'lost')),
    ADD COLUMN IF NOT EXISTS recovered_value NUMERIC
        CHECK (recovered_value IS NULL OR recovered_value >= 0),
    ADD COLUMN IF NOT EXISTS outcome_source TEXT
        CHECK (outcome_source IN ('crm', 'owner_report', 'estimated') OR outcome_source IS NULL),
    ADD COLUMN IF NOT EXISTS outcome_recorded_at TIMESTAMPTZ;

-- The monthly report and revenue_sync filter by (client_id, outcome).
CREATE INDEX IF NOT EXISTS idx_leads_client_outcome
    ON leads(client_id, outcome);

-- Per-tenant revenue-tracking config (mirrors classification_config, migration 013):
--   {"mode": "estimated" | "owner_report" | "crm", "attribution_window_days": 90}
-- 'crm' is the only mode that runs the revenue_sync CRM readback; the others
-- rely on the admin outcome endpoint (owner report) or the digest estimate.
-- Default '{}' → mode 'estimated', so a row with no revenue_config behaves
-- exactly as before this revision.
ALTER TABLE client_configs
    ADD COLUMN IF NOT EXISTS revenue_config JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMIT;
