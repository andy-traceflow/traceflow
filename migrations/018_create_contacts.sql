-- migrations/018_create_contacts.sql
--
-- Contacts: the caller identity that lives ABOVE the lead.
--
-- Until now "who is this caller" lived only on leads.phone, so a caller's
-- memory died with the lead: a callback from the same number after any
-- terminal status spawned a fresh, context-free lead and re-asked everything.
-- The contact is the durable person record — one row per (client_id, phone) —
-- that leads hang off of. It carries ONE vocabulary for "what is this caller"
-- (contact_type), with provenance (contact_type_source) and a precedence rule
-- (manual > crm > inferred) enforced in services/contacts.py.
--
-- known_facts holds PERSON-scoped durable facts only (name, address, zip,
-- preferred contact time). Project-scoped values (sqft, material, budget) die
-- with the lead and never land here.
--
-- This migration also BACKFILLS one contact per distinct (client_id, phone)
-- from the existing leads and links every lead via leads.contact_id, so the
-- table is populated, typed, and linked the moment it ships. No runtime code
-- reads it yet (that wiring is a later slice) — this migration is behavior-
-- neutral on its own.
--
-- HOW TO RUN:
--   python scripts/apply_migrations.py     (with SUPABASE_DB_URL set)

BEGIN;

-- ---------------------------------------------------------------------------
-- contacts: durable per-caller identity. See app/models/contact.py.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contacts (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id           UUID         NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    phone               TEXT         NOT NULL,                       -- E.164, normalized on write

    name                TEXT,

    -- ONE vocabulary for "what is this caller". Collapses the four competing
    -- notions (leads.classification, Route, crm_contact.ContactType) to one.
    contact_type        TEXT         NOT NULL DEFAULT 'unknown'
                                     CHECK (contact_type IN
                                       ('unknown', 'prospect', 'customer', 'vendor', 'spam', 'blocked')),
    -- Provenance. Precedence (manual > crm > inferred) is enforced in code, in
    -- ONE place: services/contacts.set_contact_type. 'blocked' is manual-only.
    contact_type_source TEXT         NOT NULL DEFAULT 'inferred'
                                     CHECK (contact_type_source IN ('manual', 'crm', 'inferred')),
    contact_type_at     TIMESTAMPTZ,                                 -- drives the CRM cache TTL (resolver)
    contact_type_reason TEXT,

    crm_external_id     TEXT,

    known_facts         JSONB        NOT NULL DEFAULT '{}'::jsonb,   -- PERSON-scoped durable facts ONLY
    summary             TEXT,                                        -- rolling, AI-written on terminal transition
    last_intent         TEXT,

    call_count          INTEGER      NOT NULL DEFAULT 0,
    lead_count          INTEGER      NOT NULL DEFAULT 0,

    first_seen_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),

    UNIQUE (client_id, phone)
);

CREATE INDEX IF NOT EXISTS idx_contacts_client_last_seen
    ON contacts(client_id, last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_contacts_client_type
    ON contacts(client_id, contact_type);

CREATE TRIGGER trg_contacts_updated_at BEFORE UPDATE ON contacts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- RLS: same pattern as every tenant-scoped table. ENABLE + FORCE (owner role
-- on Supabase bypasses plain RLS — migration 010) + NULL-safe policy
-- (empty-string tenant context collapses to zero rows, not a cast error —
-- migration 011).
ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE contacts FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON contacts
    FOR ALL
    USING (client_id = NULLIF(current_setting('app.current_client_id', true), '')::uuid);

-- ---------------------------------------------------------------------------
-- leads.contact_id — the link from a lead up to its durable contact.
-- ON DELETE SET NULL: deleting a contact must not cascade-delete its leads
-- (the lead is the business record; the contact is the index over it).
-- ---------------------------------------------------------------------------
ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS contact_id UUID REFERENCES contacts(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_leads_client_contact_created
    ON leads(client_id, contact_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Backfill: one contact per distinct (client_id, phone) in leads, then link
-- every lead. Runs as the migration runner (bypasses RLS), so it sees all
-- tenants and stamps each contact with its own client_id.
--
--   name          → most recent non-null contact_name for that number
--   first_seen_at → earliest lead created_at
--   last_seen_at  → latest lead created_at
--   lead_count    → number of leads for that number
--   contact_type  → derived from the most recent lead's classification
--                   (existing_customer→customer, known_non_lead→vendor,
--                    spam→spam, else→prospect), source 'inferred'
-- ---------------------------------------------------------------------------
INSERT INTO contacts (
    client_id, phone, name,
    contact_type, contact_type_source, contact_type_at,
    lead_count, first_seen_at, last_seen_at
)
SELECT
    l.client_id,
    l.phone,
    (SELECT l2.contact_name
       FROM leads l2
      WHERE l2.client_id = l.client_id AND l2.phone = l.phone
        AND l2.contact_name IS NOT NULL
      ORDER BY l2.created_at DESC
      LIMIT 1),
    (SELECT CASE l3.classification
        WHEN 'existing_customer' THEN 'customer'
        WHEN 'known_non_lead'    THEN 'vendor'
        WHEN 'spam'              THEN 'spam'
        ELSE 'prospect'
     END
       FROM leads l3
      WHERE l3.client_id = l.client_id AND l3.phone = l.phone
      ORDER BY l3.created_at DESC
      LIMIT 1),
    'inferred',
    max(l.created_at),
    count(*),
    min(l.created_at),
    max(l.created_at)
FROM leads l
WHERE l.phone IS NOT NULL
GROUP BY l.client_id, l.phone
ON CONFLICT (client_id, phone) DO NOTHING;

UPDATE leads l
   SET contact_id = c.id
  FROM contacts c
 WHERE c.client_id = l.client_id
   AND c.phone = l.phone
   AND l.contact_id IS NULL;

COMMIT;
