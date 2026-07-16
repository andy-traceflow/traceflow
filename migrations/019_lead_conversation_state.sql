-- migrations/019_lead_conversation_state.sql
--
-- Conversation state + returning-caller config (Slices 2 and 2.5).
--
-- Two bugs the current phone-keyed, time-unbounded open-lead query causes:
--   * Amnesia — a callback after any terminal status spawns a context-free
--     lead and re-asks everything; a qualified lead texting a follow-up with
--     no open lead is silently ignored.
--   * Stuck-open — an abandoned 'unqualified' lead from months ago still
--     matches, routes the new call to active_conversation, and never texts back.
--
-- The fix is time-bounding, which needs per-lead activity timestamps and
-- per-tenant windows. Lead-as-conversation still works — NO conversations table;
-- state is derived in code from these columns + qualification_status.
--
-- Also adds the contact source-of-truth config (Slice 2.5): where the
-- authoritative answer to "what is this caller" lives is config, resolved at
-- runtime exactly like revenue_config.mode (ADR-0003). Default 'auto' →
-- 'crm' when a working CRM adapter exists, else 'traceflow'. No second code
-- path; the contacts table is the cache in one mode and the ledger in the other.
--
-- HOW TO RUN:
--   python scripts/apply_migrations.py     (with SUPABASE_DB_URL set)

BEGIN;

-- ---------------------------------------------------------------------------
-- leads: per-lead conversation activity. Drives the resume/reopen windows.
-- ---------------------------------------------------------------------------
ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS last_inbound_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_outbound_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS turn_count        INTEGER NOT NULL DEFAULT 0;

-- Backfill from the message history: last inbound/outbound timestamps and the
-- total turn count (every message, inbound + outbound). Leads with no messages
-- keep the defaults (NULL / 0).
UPDATE leads l
   SET last_inbound_at  = m.last_in,
       last_outbound_at = m.last_out,
       turn_count       = m.cnt
  FROM (
        SELECT lead_id,
               max(created_at) FILTER (WHERE direction = 'inbound')  AS last_in,
               max(created_at) FILTER (WHERE direction = 'outbound') AS last_out,
               count(*)                                              AS cnt
          FROM messages
         GROUP BY lead_id
       ) m
 WHERE m.lead_id = l.id;

-- ---------------------------------------------------------------------------
-- client_configs.conversation_config (Slice 2). Mirrored by convenience
-- accessors on app.models.client_config.ClientConfig, matching the
-- classification_config / revenue_config pattern. Default carries every key so
-- a partial/empty object still behaves.
--   resume_window_hours          — an open lead older than this (last activity)
--                                  resumes instead of counting as an active
--                                  conversation (default 336h = 14 days)
--   reopen_window_days           — a terminal lead newer than this lets a
--                                  returning contact reopen with context (90)
--   recognize_returning_callers  — use the recognition greeting (Slice 4)
--   reuse_lead_on_resume         — resume reuses the same lead (no dup CRM record)
-- ---------------------------------------------------------------------------
ALTER TABLE client_configs
    ADD COLUMN IF NOT EXISTS conversation_config JSONB NOT NULL DEFAULT '{
        "resume_window_hours": 336,
        "reopen_window_days": 90,
        "recognize_returning_callers": true,
        "reuse_lead_on_resume": true
    }'::jsonb;

-- ---------------------------------------------------------------------------
-- client_configs.contact_config (Slice 2.5). The contact source-of-truth
-- resolver's config.
--   source_of_truth              — 'auto' | 'crm' | 'traceflow'. 'auto' →
--                                  'crm' when crm_provider has a working adapter
--                                  + creds, else 'traceflow' (no special casing
--                                  for CRMs without an adapter, e.g. ServiceTitan)
--   crm_write_back_contact_type  — OFF by default and deliberately so: TraceFlow
--                                  never writes an inferred type back to a
--                                  client's CRM. Only manual classifications are
--                                  ever eligible, and only when this is enabled.
--   contact_type_cache_days      — a cached crm-sourced type inside this TTL
--                                  skips the CRM + spam lookups entirely (the
--                                  repeat-known-caller cost win)
-- ---------------------------------------------------------------------------
ALTER TABLE client_configs
    ADD COLUMN IF NOT EXISTS contact_config JSONB NOT NULL DEFAULT '{
        "source_of_truth": "auto",
        "crm_write_back_contact_type": false,
        "contact_type_cache_days": 30
    }'::jsonb;

COMMIT;
