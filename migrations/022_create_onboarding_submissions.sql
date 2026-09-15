-- migrations/022_create_onboarding_submissions.sql
--
-- Staging table for onboarding form submissions, form-agnostic.
--
-- A submission lands here first (from Jotform today, the native form later),
-- appears on the admin side for review, and is "promoted" by the founder into
-- live clients + client_configs rows. Nothing here auto-provisions a tenant —
-- the review gate is deliberate.
--
-- This table is PRE-tenant: a row exists before any client_id does, so it is
-- NOT tenant-scoped. It is locked to the service role exactly like admin_users
-- (migration 017): RLS enabled + FORCED with zero policies = deny-all for the
-- `authenticated` role, and only the service-role connection (BYPASSRLS) —
-- used by the /api/admin surface — can read or write it.
--
-- HOW TO RUN:
--   python scripts/apply_migrations.py   (with SUPABASE_DB_URL set)
--   or paste into Supabase Dashboard > SQL Editor > Run

BEGIN;

CREATE TABLE IF NOT EXISTS onboarding_submissions (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    status              TEXT         NOT NULL DEFAULT 'new'
                                      CHECK (status IN ('new', 'reviewed', 'promoted', 'rejected')),
    source              TEXT         NOT NULL DEFAULT 'native'
                                      CHECK (source IN ('jotform', 'native')),

    -- Denormalized for the admin list view (avoids parsing raw_payload per row).
    business_name       TEXT,
    contact_email       TEXT,

    -- Full submission as received, plus the normalized draft the promote step
    -- validates into a ProvisionSpec. mapped_config is editable during review.
    raw_payload         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    mapped_config       JSONB        NOT NULL DEFAULT '{}'::jsonb,

    -- Set when promoted. ON DELETE SET NULL so off-boarding a client doesn't
    -- delete the historical submission record.
    promoted_client_id  UUID         REFERENCES clients(id) ON DELETE SET NULL,

    notes               TEXT,        -- reviewer notes

    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_onboarding_submissions_status
    ON onboarding_submissions(status);

CREATE TRIGGER trg_onboarding_submissions_updated_at BEFORE UPDATE ON onboarding_submissions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Service-role-only: deny-all under `authenticated`, no policies. Matches the
-- admin_users lockdown (migration 017). FORCE so the owning role can't skip it.
ALTER TABLE onboarding_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE onboarding_submissions FORCE ROW LEVEL SECURITY;

COMMIT;
