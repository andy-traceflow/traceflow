-- migrations/025_add_closing_templates.sql
--
-- Deterministic closing messages for a finished qualification conversation.
--
-- services/qualification.should_terminate decides when a conversation is over
-- (code owns termination, per ADR-0002). But nothing owned the CLOSING MESSAGE:
-- whatever the model happened to emit on the terminal turn was sent as-is. In
-- prod that was "Got it!" — the model's preamble before a tool call — so the
-- caller was left with no idea what happens next. Worse, the model sometimes
-- asks a follow-up question on that turn, which the system will never process
-- because the conversation has already terminated.
--
-- These two templates make the ending explicit and config-driven, mirroring
-- existing_customer_template / vendor_ack_template (migration 021):
--
--   handoff_template — the lead is real and a human should call them. MUST
--                      tell the caller a real person will follow up. Used for
--                      both `qualified` and `needs_review` terminations.
--   decline_template — a hard gate disqualified the lead (out of service area,
--                      below a disqualify_if floor). Deliberately does NOT
--                      promise a callback, because nobody is going to call.
--
-- Both nullable; NULL falls back to a sensible default in
-- prompts/greeting.py (render_handoff / render_decline). `{business_name}` is
-- substituted, same convention as the migration-021 ack templates.
--
-- HOW TO RUN:
--   python scripts/apply_migrations.py   (with SUPABASE_DB_URL set)
--   or paste into Supabase Dashboard > SQL Editor > Run

BEGIN;

ALTER TABLE client_configs
    ADD COLUMN IF NOT EXISTS handoff_template TEXT,
    ADD COLUMN IF NOT EXISTS decline_template TEXT;

COMMENT ON COLUMN client_configs.handoff_template IS
    'Closing SMS when qualification ends and a human takes over. Must tell the '
    'caller a real person will follow up. NULL → default in prompts/greeting.py. '
    'Supports {business_name}.';
COMMENT ON COLUMN client_configs.decline_template IS
    'Closing SMS when a hard gate disqualifies the lead. Does NOT promise a '
    'callback. NULL → default in prompts/greeting.py. Supports {business_name}.';

COMMIT;
