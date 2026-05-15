-- migrations/009_create_calculator_tables.sql
--
-- Generic quote/estimate calculator engine for SIA Module B. Stores
-- per-client product yields and finish configurations. The math is
-- run by app/services/calculator.py; this migration only defines the
-- shape.
--
-- Per-client tenant isolation: a flooring contractor's product catalog
-- looks nothing like a pool resurfacer's, but both fit the same
-- (product_name × pack_size × yield_per_unit) schema. Per-finish addon
-- requirements live in calculator_configs.required_addons JSONB so the
-- engine can be extended without schema changes.
--
-- HOW TO RUN:
--   1. Open Supabase Dashboard > SQL Editor > New query
--   2. Paste this entire file
--   3. Click Run

BEGIN;

-- ---------------------------------------------------------------------------
-- product_yields: every product/pack-size combo with price, weight, coverage
--
-- For products whose yield depends on the finish (e.g. a base material
-- yielding more sqft on a thin finish than on a textured one), insert
-- one row per (product_name, sku_size, finish_group). The calculator
-- looks up rows by finish_group at quote time.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_yields (
    id                     BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id              UUID         NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    product_name           TEXT         NOT NULL,
    sku_size               TEXT         NOT NULL,             -- pack/container size descriptor
    weight_lbs             NUMERIC,
    price_retail           NUMERIC      NOT NULL,
    price_wholesale        NUMERIC,                            -- nullable; populated when tier pricing is in scope
    coverage_per_unit      NUMERIC,                            -- NULL = yield depends on user choice (e.g. activator)
    coverage_unit          TEXT         NOT NULL DEFAULT 'sqft',  -- 'sqft' | 'linear_ft' | 'each' | etc.
    finish_group           TEXT         NOT NULL DEFAULT 'all',
    product_category       TEXT         NOT NULL,             -- per-client taxonomy
    pack_size              TEXT         NOT NULL DEFAULT 'small'
                                        CHECK (pack_size IN ('small', 'large')),
    external_variant_id    TEXT,                               -- e.g. Shopify variant ID; populated when push-to-order is wired
    notes                  TEXT,
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (client_id, product_name, sku_size, finish_group)
);

CREATE INDEX IF NOT EXISTS idx_product_yields_lookup
    ON product_yields(client_id, finish_group, product_category, pack_size);

CREATE TRIGGER trg_product_yields_updated_at BEFORE UPDATE ON product_yields
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE product_yields ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON product_yields
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid);


-- ---------------------------------------------------------------------------
-- calculator_configs: per-finish rules (which addon categories are required)
--
-- required_addons is a JSONB array of product_category strings the engine
-- will auto-add. Adding a new addon type (e.g. 'primer') needs no schema
-- change — just a config update and matching product_yields rows.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calculator_configs (
    client_id        UUID         NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    finish_type      TEXT         NOT NULL,
    finish_group     TEXT         NOT NULL,
    display_name     TEXT         NOT NULL,
    required_addons  JSONB        NOT NULL DEFAULT '[]'::jsonb,            -- product_category strings the engine auto-adds, e.g. ["primer", "sealer"]
    sort_order       INTEGER      NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, finish_type)
);

CREATE INDEX IF NOT EXISTS idx_calculator_configs_client
    ON calculator_configs(client_id, sort_order);

CREATE TRIGGER trg_calculator_configs_updated_at BEFORE UPDATE ON calculator_configs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE calculator_configs ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON calculator_configs
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid);

COMMIT;
