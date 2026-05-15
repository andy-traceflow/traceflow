-- migrations/001_create_clients_and_configs.sql
--
-- Foundation tables for multi-tenancy. Every other tenant-scoped table
-- references clients(id) via a client_id FK with ON DELETE CASCADE so
-- off-boarding a client cleans up their data atomically.
--
-- HOW TO RUN:
--   1. Open Supabase Dashboard > SQL Editor > New query
--   2. Paste this entire file
--   3. Click Run

BEGIN;

-- ---------------------------------------------------------------------------
-- clients: one row per tenant
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clients (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            TEXT         UNIQUE NOT NULL,             -- url-safe handle
    business_name   TEXT         NOT NULL,
    status          TEXT         NOT NULL DEFAULT 'active'
                                  CHECK (status IN ('active', 'paused', 'churned', 'trial')),
    tier            TEXT         NOT NULL DEFAULT 'standard'
                                  CHECK (tier IN ('founding_partner', 'standard', 'pro', 'full_stack')),
    timezone        TEXT         NOT NULL DEFAULT 'America/Los_Angeles',
    signed_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    launched_at     TIMESTAMPTZ,
    churned_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_clients_status ON clients(status) WHERE status = 'active';

-- ---------------------------------------------------------------------------
-- client_configs: per-tenant runtime configuration
--
-- Everything that varies between clients lives here. Code reads from these
-- columns rather than from per-client `if` branches. See the inviolable rule
-- in CLAUDE.md.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS client_configs (
    client_id                 UUID         PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,

    -- Operational settings
    business_hours            JSONB        NOT NULL DEFAULT '{}'::jsonb,  -- per-day open/close, e.g. {"mon": {"open": "08:00", "close": "17:00"}}
    service_area_zips         TEXT[]       NOT NULL DEFAULT '{}',
    twilio_number             TEXT         UNIQUE,                          -- E.164 format
    vip_keywords              TEXT[]       NOT NULL DEFAULT '{}',
    vip_value_threshold       NUMERIC,                                      -- dollars; trips owner alert if quoted job exceeds

    -- Integration routing
    crm_provider              TEXT         CHECK (crm_provider IN ('ghl', 'hubspot', 'monday', 'generic') OR crm_provider IS NULL),
    crm_credentials           JSONB,                                        -- encrypted at rest; provider-specific shape
    webhook_signing_secrets   JSONB        NOT NULL DEFAULT '{}'::jsonb,    -- {"twilio": "...", "shopify": "...", ...}

    -- AI behavior
    qualification_prompt      TEXT,                                         -- override (otherwise default Jinja template applies)
    greeting_template         TEXT,                                         -- override
    prompt_versions           JSONB        NOT NULL DEFAULT '{}'::jsonb,    -- {"greeting": "v2", "qualifier": "v1"}
    ai_interaction_cap_monthly INTEGER     NOT NULL DEFAULT 1000,
    ai_interactions_used      INTEGER      NOT NULL DEFAULT 0,
    ai_period_resets_at       TIMESTAMPTZ  NOT NULL DEFAULT date_trunc('month', now() + interval '1 month'),

    -- Branding
    brand                     JSONB        NOT NULL DEFAULT '{}'::jsonb,    -- {"logo_url", "primary_color", "tone_of_voice", "service_types": [], "category"}

    -- Notification delivery
    notification_emails       TEXT[]       NOT NULL DEFAULT '{}',
    owner_alert_emails        TEXT[]       NOT NULL DEFAULT '{}',
    owner_alert_phones        TEXT[]       NOT NULL DEFAULT '{}',           -- E.164

    -- Feature flags
    feature_flags             JSONB        NOT NULL DEFAULT '{}'::jsonb,

    updated_at                TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- updated_at trigger (shared helper, used by every table going forward)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_clients_updated_at        BEFORE UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_client_configs_updated_at BEFORE UPDATE ON client_configs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- Row Level Security
--
-- The tenant_resolver middleware sets `app.current_client_id` on every
-- request. RLS filters every tenant-scoped query by that value. The
-- middleware uses the Supabase service role for admin lookups (which
-- bypasses RLS) and explicitly sets the tenant context per request.
-- ---------------------------------------------------------------------------
ALTER TABLE clients        ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_configs ENABLE ROW LEVEL SECURITY;

-- clients: a tenant can read its own row. Admin operations use service role.
CREATE POLICY tenant_isolation ON clients
    FOR ALL
    USING (id = current_setting('app.current_client_id', true)::uuid);

CREATE POLICY tenant_isolation ON client_configs
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid);

COMMIT;
