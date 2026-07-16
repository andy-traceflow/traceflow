-- migrations/021_add_ack_templates.sql
--
-- Distinct acknowledgment templates for the two non-lead routes (Slice 4).
-- An existing customer at voicemail and a vendor are NOT the same message: the
-- customer gets a service acknowledgment (and their business is alerted), the
-- vendor gets a bare ack or nothing. Both nullable — greeting.render_*_ack
-- falls back to a sensible default when unset.
--
-- HOW TO RUN:
--   python scripts/apply_migrations.py     (with SUPABASE_DB_URL set)

BEGIN;

ALTER TABLE client_configs
    ADD COLUMN IF NOT EXISTS existing_customer_template TEXT,
    ADD COLUMN IF NOT EXISTS vendor_ack_template TEXT;

COMMIT;
