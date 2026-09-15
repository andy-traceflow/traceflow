-- migrations/024_backfill_lead_notes.sql
--
-- Fix: NULL leads.notes crashed the inbound-SMS reply path.
--
-- `leads.notes` was created nullable with no default (migration 004), but the
-- canonical Lead model declares `notes: str = ""` — "always a string", because
-- the CRM adapters push `lead.notes` directly into GHL/HubSpot/Monday payloads.
--
-- Any lead inserted without an explicit notes value (e.g. the missed-call and
-- inbound-SMS paths in webhooks/twilio.py, which don't set it) read back as
-- None, and `Lead(**dict(lead_row))` raised:
--     ValidationError: notes — Input should be a valid string [input_value=None]
-- That 500'd every SMS reply, so a real caller never got past the first
-- qualification question. Found in prod 2026-07-21.
--
-- This backfills existing NULLs and defaults the column so new rows are
-- consistent. The column stays NULLABLE deliberately: LeadUpdate.notes uses
-- None to mean "field not provided" in partial updates, and a NOT NULL
-- constraint would turn any unaudited write path into a hard failure. The
-- model-side coercion (models/lead.py::_coerce_null_notes) is the belt to this
-- migration's braces, and covers rows written before this ran.
--
-- HOW TO RUN:
--   python scripts/apply_migrations.py   (with SUPABASE_DB_URL set)
--   or paste into Supabase Dashboard > SQL Editor > Run

BEGIN;

UPDATE leads SET notes = '' WHERE notes IS NULL;

ALTER TABLE leads ALTER COLUMN notes SET DEFAULT '';

COMMIT;
