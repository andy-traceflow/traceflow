-- migrations/020_add_qualification_schema.sql
--
-- Config-driven qualification (Slice 3). The qualifier's field list, weights,
-- and questions move out of code and into client_configs.qualification_schema,
-- so a client-specific field (material, project stage, property type) is a
-- config row, not a code change. The free-text client_configs.qualification_prompt
-- was the wrong primitive and nothing read it — it is deprecated here (dropped
-- in a later migration).
--
-- leads.qualification_score is REPURPOSED as the completeness score (captured
-- required weight / applicable required weight × 100) — it was declared in
-- migration 004 and never written by anything. value_score is a SEPARATE,
-- deterministic estimate of the job's value. They are never blended: a fully
-- captured $700 backsplash is 100% complete and near-zero value, and one merged
-- number would destroy the client's trust in the digest.
--
-- 'disqualified' joins the terminal statuses: a hard-gate failure (out of
-- service area, below a disqualify_if floor) is a disqualification, not spam
-- and not a needs-review.
--
-- The seeded qualification_schema mirrors app.models.qualification
-- .DEFAULT_QUALIFICATION_SCHEMA_DICT — keep the two in sync.
--
-- HOW TO RUN:
--   python scripts/apply_migrations.py     (with SUPABASE_DB_URL set)

BEGIN;

ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS qualification_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS value_score INTEGER
        CHECK (value_score IS NULL OR value_score BETWEEN 0 AND 100);

-- Widen the terminal-status set (see migration 015 for the same pattern).
ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_qualification_status_check;
ALTER TABLE leads ADD CONSTRAINT leads_qualification_status_check
    CHECK (qualification_status IN (
        'unqualified',
        'qualifying',
        'qualified',
        'high_value',
        'needs_review',
        'spam',
        'duplicate',
        'support_touch',
        'non_lead_contact',
        'disqualified'
    ));

ALTER TABLE client_configs
    ADD COLUMN IF NOT EXISTS qualification_schema JSONB NOT NULL DEFAULT '{
        "min_score_to_qualify": 60,
        "max_turns": 8,
        "max_questions_per_message": 1,
        "ask_budget": false,
        "fields": [
            {"key": "contact_name", "label": "Name", "type": "string", "scope": "person",
             "required": true, "weight": 10, "maps_to": "contact_name",
             "ask": "Can I grab your name so I can let the team know who to reach out to?"},
            {"key": "zip", "label": "ZIP code", "type": "string", "scope": "person",
             "required": true, "weight": 15, "maps_to": "address", "hard_gate": "service_area",
             "ask": "What ZIP code is the project in? Want to make sure it is in our service area."},
            {"key": "service_type", "label": "Service", "type": "enum", "scope": "project",
             "required": true, "weight": 20, "maps_to": "service_type",
             "options": ["countertop", "flooring", "tile", "cabinets", "backsplash", "other"],
             "ask": "What kind of work are you looking to have done?"},
            {"key": "material", "label": "Material", "type": "enum", "scope": "project",
             "required": true, "weight": 15,
             "options": ["quartz", "granite", "quartzite", "marble", "porcelain",
                         "lvp", "tile", "concrete", "unsure"],
             "depends_on": {"service_type": ["countertop", "flooring", "tile"]},
             "ask": "Do you have a material in mind (quartz, granite, tile, etc.), or still deciding?"},
            {"key": "scope_size", "label": "Size", "type": "number", "scope": "project",
             "required": true, "weight": 15, "unit": "sqft", "maps_to": "sqft",
             "disqualify_if": {"lt": 10},
             "ask": "Roughly how many square feet is the project?"},
            {"key": "timeframe", "label": "Timeframe", "type": "enum", "scope": "project",
             "required": true, "weight": 15, "maps_to": "timeframe",
             "options": ["asap", "this_month", "this_quarter", "researching"],
             "ask": "When are you hoping to get this done?"},
            {"key": "project_stage", "label": "Stage", "type": "enum", "scope": "project",
             "required": true, "weight": 10,
             "options": ["pricing", "have_measurements", "ready_to_schedule"],
             "ask": "Where are you in the process - just pricing it out, have measurements, or ready to schedule?"},
            {"key": "budget_range", "label": "Budget", "type": "enum", "scope": "project",
             "required": false, "weight": 0, "maps_to": "budget_range",
             "options": ["<5k", "5k-15k", "15k-50k", "50k+"],
             "ask": "Do you have a budget range in mind?"},
            {"key": "property_type", "label": "Property", "type": "enum", "scope": "person",
             "required": false, "weight": 0,
             "options": ["residential", "commercial", "new_construction", "remodel"],
             "ask": "Is this a residential or commercial property?"},
            {"key": "tear_out_needed", "label": "Tear-out", "type": "boolean", "scope": "project",
             "required": false, "weight": 0,
             "ask": "Is there existing material that needs to be torn out first?"}
        ]
    }'::jsonb;

COMMENT ON COLUMN client_configs.qualification_prompt IS
    'DEPRECATED - superseded by qualification_schema. Unread. Drop in a later migration.';

COMMIT;
