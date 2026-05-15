-- migrations/004_create_leads_messages_events.sql
--
-- Canonical schema. Every lead in the system — regardless of source
-- (Twilio missed call, website form, Shopify order, generic webhook) —
-- looks the same shape internally. Adapters at the edges translate
-- into this; the rest of the pipeline reasons about it uniformly.
--
-- raw_payload is preserved on every record. Debugging an integration
-- failure without the original payload is misery.
--
-- HOW TO RUN:
--   1. Open Supabase Dashboard > SQL Editor > New query
--   2. Paste this entire file
--   3. Click Run

BEGIN;

-- ---------------------------------------------------------------------------
-- leads: the canonical Lead. See app/models/lead.py for the Python shape.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS leads (
    id                     UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id              UUID         NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    external_id            TEXT,                                          -- ID in the client's CRM after push
    source_system          TEXT         NOT NULL,                         -- 'twilio_missed_call', 'shopify', 'website_form', 'manual', 'monday', etc.

    -- Contact
    contact_name           TEXT,
    contact_company        TEXT,
    phone                  TEXT,
    email                  TEXT,
    address                TEXT,

    -- Project
    service_type           TEXT,                                          -- per-client taxonomy; lives in client_configs.brand.service_types
    sqft                   NUMERIC,
    budget_range           TEXT         CHECK (budget_range IN ('<5k', '5k-15k', '15k-50k', '50k+') OR budget_range IS NULL),
    timeframe              TEXT         CHECK (timeframe IN ('asap', 'this_month', 'this_quarter', 'researching') OR timeframe IS NULL),

    -- Qualification
    qualification_status   TEXT         NOT NULL DEFAULT 'unqualified'
                                        CHECK (qualification_status IN ('unqualified', 'qualifying', 'qualified', 'high_value', 'needs_review', 'spam', 'duplicate')),
    qualification_score    INTEGER      CHECK (qualification_score IS NULL OR qualification_score BETWEEN 0 AND 100),

    -- Free-form
    notes                  TEXT,

    -- Always preserved — debugging gold
    raw_payload            JSONB        NOT NULL,

    -- Timestamps
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    qualified_at           TIMESTAMPTZ,
    pushed_to_crm_at       TIMESTAMPTZ,
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_leads_client_created
    ON leads(client_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_leads_client_status
    ON leads(client_id, qualification_status);

CREATE INDEX IF NOT EXISTS idx_leads_phone
    ON leads(client_id, phone) WHERE phone IS NOT NULL;

CREATE TRIGGER trg_leads_updated_at BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON leads
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid);


-- ---------------------------------------------------------------------------
-- messages: every inbound/outbound message tied to a lead
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID         NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    lead_id         UUID         NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    direction       TEXT         NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    channel         TEXT         NOT NULL CHECK (channel IN ('sms', 'email', 'chat', 'voice')),
    body            TEXT         NOT NULL,
    ai_generated    BOOLEAN      NOT NULL DEFAULT FALSE,
    prompt_version  TEXT,                                                  -- which prompt produced this, if AI-generated
    raw_payload     JSONB,                                                 -- Twilio webhook body, email source, etc.
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_messages_lead_created
    ON messages(lead_id, created_at);

CREATE INDEX IF NOT EXISTS idx_messages_client_created
    ON messages(client_id, created_at DESC);

ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON messages
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid);


-- ---------------------------------------------------------------------------
-- events: low-level event stream for debugging + analytics
--
-- Anything noteworthy that happens in the system — webhook received,
-- SMS sent, CRM pushed, qualifier ran, etc. — drops a row here with
-- its full payload. RLS-scoped so per-client event review is safe.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID         NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    lead_id         UUID         REFERENCES leads(id) ON DELETE SET NULL,
    event_type      TEXT         NOT NULL,
    payload         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_events_client_type_created
    ON events(client_id, event_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_lead
    ON events(lead_id) WHERE lead_id IS NOT NULL;

ALTER TABLE events ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON events
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid);

COMMIT;
