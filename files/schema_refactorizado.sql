-- ============================================================================
-- Qargo Coffee — Refactored schema (target state)
-- PostgreSQL 15+
-- ----------------------------------------------------------------------------
-- IMPORTANT (read before running):
--   1. This is a GREENFIELD / target-state script. The live database is managed
--      by Alembic (table public.alembic_version). Do NOT drop & recreate the
--      production schema with this file. Translate each change below into
--      incremental Alembic migrations. Several changes (adding NOT NULL, CHECK,
--      UNIQUE, FK actions) require data backfill / de-duplication FIRST.
--   2. On an empty database this file runs top to bottom inside one transaction.
--      On an existing DB, index creation should use CREATE INDEX CONCURRENTLY
--      (outside a transaction) to avoid write locks.
--   3. The original dump came from an introspection tool that strips length and
--      precision modifiers (varchar -> "character varying", numeric(p,s) ->
--      "numeric", char(n) -> "character"). Correct types are re-established here.
--
-- FK ON DELETE policy adopted:
--   - CASCADE  : child rows are meaningless without the parent (recipe lines,
--                sizes, junctions, route prices).
--   - RESTRICT : block deletion of a dimension still referenced as a business
--                fact (an ingredient used in recipes, a product used as audit
--                target). Dimensions use soft-delete via is_active anyway.
--   - SET NULL : optional / nullable references (region, modifier source).
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 0. Extensions
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS btree_gist;  -- required by EXCLUDE constraints on (id, daterange)
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- optional: fuzzy ILIKE search on names

-- ---------------------------------------------------------------------------
-- 1. Domains — single source of truth for numeric semantics
-- ---------------------------------------------------------------------------
-- Monetary values: 14 digits / 4 decimals. Non-negative by construction.
-- COP has no minor unit but other currencies (USD/EUR) do, hence 4 decimals.
CREATE DOMAIN price_amount    AS numeric(14, 4) CHECK (VALUE >= 0);
-- Quantities & conversion factors: extra decimals for unit math (g per oz, etc.).
CREATE DOMAIN quantity_amount AS numeric(14, 6) CHECK (VALUE >= 0);
-- Percentages / markups. Signed (impacts can be negative); bounds applied per column.
CREATE DOMAIN pct_amount      AS numeric(6, 3);
-- ISO 3166-1 alpha-2 country code.
CREATE DOMAIN iso_country     AS char(2) CHECK (VALUE ~ '^[A-Z]{2}$');

-- ---------------------------------------------------------------------------
-- 2. Shared trigger function: keep updated_at honest on every UPDATE
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

-- ===========================================================================
-- DOMAIN: Reference / lookup tables (no FK dependencies)
-- ===========================================================================

-- ISO 4217 currency catalog. Normalizing currency removes the inconsistent
-- "character varying DEFAULT 'COP'" vs "character CHECK (~ '^[A-Z]{3}$')" split
-- and gives every monetary table a real FK target.
CREATE TABLE currencies (
  code           char(3) NOT NULL,
  name           varchar(64) NOT NULL,
  minor_unit     smallint NOT NULL DEFAULT 2 CHECK (minor_unit BETWEEN 0 AND 4),
  is_active      boolean NOT NULL DEFAULT true,
  CONSTRAINT pk_currencies PRIMARY KEY (code),
  CONSTRAINT ck_currencies_code CHECK (code ~ '^[A-Z]{3}$')
);
INSERT INTO currencies (code, name, minor_unit) VALUES
  ('COP', 'Colombian Peso', 0),
  ('USD', 'US Dollar', 2),
  ('EUR', 'Euro', 2);

-- Product categories. Slug retained as the natural key (it is stable and used
-- across the app), but referencing FKs now use ON UPDATE CASCADE so a slug
-- rename propagates safely. Alternative: surrogate id + UNIQUE(slug).
CREATE TABLE categories (
  slug          varchar(80) NOT NULL,
  display_name  varchar(160),
  notes         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_categories PRIMARY KEY (slug),
  CONSTRAINT ck_categories_slug_format CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$')
);

CREATE TABLE regions (
  id            bigint GENERATED ALWAYS AS IDENTITY,
  name          varchar(120) NOT NULL,
  code          varchar(40)  NOT NULL,
  country_code  iso_country  NOT NULL DEFAULT 'CO',
  is_active     boolean NOT NULL DEFAULT true,
  metadata      jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_regions PRIMARY KEY (id),
  CONSTRAINT uq_regions_code UNIQUE (code)
);

CREATE TABLE recipe_units (
  id           bigint GENERATED ALWAYS AS IDENTITY,
  name         varchar(60) NOT NULL,
  category     varchar(60),
  description  text,
  is_active    boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_recipe_units PRIMARY KEY (id),
  CONSTRAINT uq_recipe_units_name UNIQUE (name)
);

CREATE TABLE ingredients (
  id                 bigint GENERATED ALWAYS AS IDENTITY,
  name               varchar(180) NOT NULL,
  category           varchar(80),
  purchase_unit      varchar(40),
  purchase_price     price_amount,
  usage_unit         varchar(40),
  conversion_factor  quantity_amount CHECK (conversion_factor IS NULL OR conversion_factor > 0),
  yield_percentage   pct_amount CHECK (yield_percentage IS NULL OR yield_percentage BETWEEN 0 AND 100),
  canonical_unit     varchar(40),
  source_url         text,
  last_scraped       timestamptz,
  is_active          boolean NOT NULL DEFAULT true,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_ingredients PRIMARY KEY (id)
);

CREATE TABLE manufacturers (
  id            bigint GENERATED ALWAYS AS IDENTITY,
  name          varchar(160) NOT NULL,
  country_code  iso_country  NOT NULL DEFAULT 'CO',
  tax_id        varchar(40),
  website       text,
  is_active     boolean NOT NULL DEFAULT true,
  metadata      jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_manufacturers PRIMARY KEY (id)
);

CREATE TABLE distributors (
  id             bigint GENERATED ALWAYS AS IDENTITY,
  name           varchar(160) NOT NULL,
  country_code   iso_country  NOT NULL DEFAULT 'CO',
  tax_id         varchar(40),
  contact_email  varchar(160),
  contact_phone  varchar(40),
  is_active      boolean NOT NULL DEFAULT true,
  metadata       jsonb,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_distributors PRIMARY KEY (id),
  CONSTRAINT ck_distributors_email CHECK (contact_email IS NULL OR contact_email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$')
);

CREATE TABLE competitors (
  id           bigint GENERATED ALWAYS AS IDENTITY,
  name         varchar(160) NOT NULL,
  website_url  text,
  is_active    boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_competitors PRIMARY KEY (id)
);

CREATE TABLE modifiers (
  id           bigint GENERATED ALWAYS AS IDENTITY,
  name         varchar(120) NOT NULL,
  type         varchar(60),
  is_active    boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_modifiers PRIMARY KEY (id)
);

-- ===========================================================================
-- DOMAIN: Stores & geography
-- ===========================================================================
CREATE TABLE stores (
  id                    bigint GENERATED ALWAYS AS IDENTITY,
  code                  varchar(40)  NOT NULL,
  name                  varchar(160) NOT NULL,
  city                  varchar(120),
  region_id             bigint,
  default_currency_code char(3) NOT NULL DEFAULT 'COP',
  is_active             boolean NOT NULL DEFAULT true,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_stores PRIMARY KEY (id),
  CONSTRAINT uq_stores_code UNIQUE (code),
  CONSTRAINT fk_stores_region   FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE SET NULL,
  CONSTRAINT fk_stores_currency FOREIGN KEY (default_currency_code) REFERENCES currencies(code) ON UPDATE CASCADE ON DELETE RESTRICT
);

-- ===========================================================================
-- DOMAIN: Product & recipe catalog
-- ===========================================================================
CREATE TABLE products (
  id                    bigint GENERATED ALWAYS AS IDENTITY,
  name                  varchar(180) NOT NULL,
  category              varchar(80),                 -- FK -> categories(slug)
  base_size_oz          numeric(10, 3) CHECK (base_size_oz IS NULL OR base_size_oz > 0),
  prep_time_minutes     numeric(8, 2)  CHECK (prep_time_minutes IS NULL OR prep_time_minutes >= 0),
  labor_cost_per_minute price_amount,
  is_sub_recipe         boolean NOT NULL DEFAULT false,
  is_active             boolean NOT NULL DEFAULT true,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_products PRIMARY KEY (id),
  CONSTRAINT fk_products_category FOREIGN KEY (category)
    REFERENCES categories(slug) ON UPDATE CASCADE ON DELETE SET NULL
);

CREATE TABLE category_margins (
  id                bigint GENERATED ALWAYS AS IDENTITY,
  category          varchar(80) NOT NULL,
  markup_percentage pct_amount  NOT NULL CHECK (markup_percentage >= 0),
  notes             text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_category_margins PRIMARY KEY (id),
  CONSTRAINT uq_category_margins_category UNIQUE (category),
  CONSTRAINT fk_category_margins_category FOREIGN KEY (category)
    REFERENCES categories(slug) ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE TABLE product_sizes (
  id           bigint GENERATED ALWAYS AS IDENTITY,
  product_id   bigint NOT NULL,
  size_name    varchar(60) NOT NULL,
  volume_oz    numeric(10, 3) CHECK (volume_oz IS NULL OR volume_oz > 0),
  scale_factor quantity_amount CHECK (scale_factor IS NULL OR scale_factor > 0),
  is_default   boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_product_sizes PRIMARY KEY (id),
  CONSTRAINT uq_product_sizes_name UNIQUE (product_id, size_name),
  CONSTRAINT fk_product_sizes_product FOREIGN KEY (product_id)
    REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE recipe_ingredients (
  id                bigint GENERATED ALWAYS AS IDENTITY,
  product_id        bigint NOT NULL,
  ingredient_id     bigint NOT NULL,
  quantity          quantity_amount NOT NULL CHECK (quantity > 0),
  recipe_unit_id    bigint,
  scales_with_size  boolean NOT NULL DEFAULT true,
  process_yield_loss pct_amount CHECK (process_yield_loss IS NULL OR process_yield_loss BETWEEN 0 AND 100),
  notes             text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_recipe_ingredients PRIMARY KEY (id),
  CONSTRAINT uq_recipe_ingredients UNIQUE (product_id, ingredient_id),
  CONSTRAINT fk_recipe_ingredients_product    FOREIGN KEY (product_id)     REFERENCES products(id)     ON DELETE CASCADE,
  CONSTRAINT fk_recipe_ingredients_ingredient FOREIGN KEY (ingredient_id)  REFERENCES ingredients(id)  ON DELETE RESTRICT,
  CONSTRAINT fk_recipe_ingredients_unit       FOREIGN KEY (recipe_unit_id) REFERENCES recipe_units(id) ON DELETE SET NULL
);

-- Self-referential BOM. Guards added: a product cannot be its own sub-recipe,
-- and (parent, sub) is unique. Deep cycle detection (A->B->A) must be enforced
-- in the application or a recursive trigger; a CHECK cannot express it.
CREATE TABLE recipe_sub_recipes (
  id                bigint GENERATED ALWAYS AS IDENTITY,
  parent_product_id bigint NOT NULL,
  sub_recipe_id     bigint NOT NULL,
  quantity          quantity_amount NOT NULL CHECK (quantity > 0),
  scales_with_size  boolean NOT NULL DEFAULT true,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_recipe_sub_recipes PRIMARY KEY (id),
  CONSTRAINT uq_recipe_sub_recipes UNIQUE (parent_product_id, sub_recipe_id),
  CONSTRAINT ck_recipe_sub_recipes_no_self CHECK (parent_product_id <> sub_recipe_id),
  CONSTRAINT fk_recipe_sub_recipes_parent FOREIGN KEY (parent_product_id) REFERENCES products(id) ON DELETE CASCADE,
  CONSTRAINT fk_recipe_sub_recipes_sub    FOREIGN KEY (sub_recipe_id)     REFERENCES products(id) ON DELETE RESTRICT
);

CREATE TABLE size_packaging (
  id                      bigint GENERATED ALWAYS AS IDENTITY,
  size_id                 bigint NOT NULL,
  packaging_ingredient_id bigint NOT NULL,
  quantity                quantity_amount NOT NULL DEFAULT 1 CHECK (quantity > 0),
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_size_packaging PRIMARY KEY (id),
  CONSTRAINT uq_size_packaging UNIQUE (size_id, packaging_ingredient_id),
  CONSTRAINT fk_size_packaging_size FOREIGN KEY (size_id)
    REFERENCES product_sizes(id) ON DELETE CASCADE,
  CONSTRAINT fk_size_packaging_ingredient FOREIGN KEY (packaging_ingredient_id)
    REFERENCES ingredients(id) ON DELETE RESTRICT
);

CREATE TABLE modifier_ingredient_effects (
  id              bigint GENERATED ALWAYS AS IDENTITY,
  modifier_id     bigint NOT NULL,
  ingredient_id   bigint NOT NULL,
  quantity_change numeric(14, 6) NOT NULL,  -- signed: a modifier may add (+) or remove (-) an ingredient, so the non-negative quantity_amount domain cannot be used here
  CONSTRAINT ck_mie_quantity_change_nonzero CHECK (quantity_change <> 0),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_modifier_ingredient_effects PRIMARY KEY (id),
  CONSTRAINT uq_modifier_ingredient_effects UNIQUE (modifier_id, ingredient_id),
  CONSTRAINT fk_mie_modifier   FOREIGN KEY (modifier_id)   REFERENCES modifiers(id)   ON DELETE CASCADE,
  CONSTRAINT fk_mie_ingredient FOREIGN KEY (ingredient_id) REFERENCES ingredients(id) ON DELETE RESTRICT
);

-- product_modifier_costs: cost_impact is DERIVED (modifier effects x ingredient
-- prices). Kept as an optional pre-computed cache; v_product_modifier_cost below
-- recomputes it live. Drop this table if you prefer the view as source of truth.
CREATE TABLE product_modifier_costs (
  id            bigint GENERATED ALWAYS AS IDENTITY,
  product_id    bigint NOT NULL,
  modifier_id   bigint NOT NULL,
  cost_impact   numeric(14, 4) NOT NULL,  -- signed
  calculated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_product_modifier_costs PRIMARY KEY (id),
  CONSTRAINT uq_product_modifier_costs UNIQUE (product_id, modifier_id),
  CONSTRAINT fk_pmc_product  FOREIGN KEY (product_id)  REFERENCES products(id)  ON DELETE CASCADE,
  CONSTRAINT fk_pmc_modifier FOREIGN KEY (modifier_id) REFERENCES modifiers(id) ON DELETE CASCADE
);

-- ===========================================================================
-- DOMAIN: Ingredient catalog extensions
-- ===========================================================================
CREATE TABLE ingredient_recipe_unit_conversions (
  id                  bigint GENERATED ALWAYS AS IDENTITY,
  ingredient_id       bigint NOT NULL,
  recipe_unit_id      bigint NOT NULL,
  usage_unit_quantity quantity_amount NOT NULL CHECK (usage_unit_quantity > 0),
  notes               text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_iruc PRIMARY KEY (id),
  CONSTRAINT uq_iruc UNIQUE (ingredient_id, recipe_unit_id),
  CONSTRAINT fk_iruc_ingredient FOREIGN KEY (ingredient_id)  REFERENCES ingredients(id)  ON DELETE CASCADE,
  CONSTRAINT fk_iruc_unit       FOREIGN KEY (recipe_unit_id) REFERENCES recipe_units(id) ON DELETE RESTRICT
);

CREATE TABLE ingredient_substitutes (
  id                       bigint GENERATED ALWAYS AS IDENTITY,
  original_ingredient_id   bigint NOT NULL,
  substitute_ingredient_id bigint NOT NULL,
  approved_by              varchar(120) NOT NULL,
  approval_date            date NOT NULL,
  activation_condition     varchar(20) NOT NULL DEFAULT 'shortage'
    CHECK (activation_condition IN ('shortage', 'unavailable', 'always')),
  quantity_ratio           quantity_amount NOT NULL DEFAULT 1.0 CHECK (quantity_ratio > 0),
  recipe_unit_id           bigint,
  cost_impact_pct          pct_amount,
  valid_from               date NOT NULL DEFAULT CURRENT_DATE,
  valid_until              date,
  notes                    text,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_ingredient_substitutes PRIMARY KEY (id),
  CONSTRAINT uq_ingredient_substitutes UNIQUE (original_ingredient_id, substitute_ingredient_id),
  CONSTRAINT ck_ingredient_substitutes_no_self CHECK (original_ingredient_id <> substitute_ingredient_id),
  CONSTRAINT ck_ingredient_substitutes_validity CHECK (valid_until IS NULL OR valid_until >= valid_from),
  CONSTRAINT fk_isub_original   FOREIGN KEY (original_ingredient_id)   REFERENCES ingredients(id)  ON DELETE CASCADE,
  CONSTRAINT fk_isub_substitute FOREIGN KEY (substitute_ingredient_id) REFERENCES ingredients(id)  ON DELETE RESTRICT,
  CONSTRAINT fk_isub_unit       FOREIGN KEY (recipe_unit_id)           REFERENCES recipe_units(id) ON DELETE SET NULL
);

-- Replaces the original "affects_regions ARRAY" column. An array of region ids
-- cannot enforce referential integrity; this junction can. Empty set = global.
CREATE TABLE ingredient_substitute_regions (
  substitute_id bigint NOT NULL,
  region_id     bigint NOT NULL,
  CONSTRAINT pk_isub_regions PRIMARY KEY (substitute_id, region_id),
  CONSTRAINT fk_isr_substitute FOREIGN KEY (substitute_id) REFERENCES ingredient_substitutes(id) ON DELETE CASCADE,
  CONSTRAINT fk_isr_region     FOREIGN KEY (region_id)     REFERENCES regions(id)                 ON DELETE CASCADE
);

-- ===========================================================================
-- DOMAIN: Supply chain
-- ===========================================================================
CREATE TABLE supply_routes (
  id              bigint GENERATED ALWAYS AS IDENTITY,
  ingredient_id   bigint NOT NULL,
  manufacturer_id bigint,
  distributor_id  bigint,
  is_direct       boolean NOT NULL DEFAULT false,
  is_active       boolean NOT NULL DEFAULT true,
  metadata        jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_supply_routes PRIMARY KEY (id),
  -- A route must resolve to at least one supplier endpoint.
  CONSTRAINT ck_supply_routes_endpoint CHECK (manufacturer_id IS NOT NULL OR distributor_id IS NOT NULL),
  CONSTRAINT fk_sr_ingredient   FOREIGN KEY (ingredient_id)   REFERENCES ingredients(id)   ON DELETE RESTRICT,
  CONSTRAINT fk_sr_manufacturer FOREIGN KEY (manufacturer_id) REFERENCES manufacturers(id) ON DELETE SET NULL,
  CONSTRAINT fk_sr_distributor  FOREIGN KEY (distributor_id)  REFERENCES distributors(id)  ON DELETE SET NULL
);

CREATE TABLE supply_route_assignments (
  id              bigint GENERATED ALWAYS AS IDENTITY,
  supply_route_id bigint NOT NULL,
  region_id       bigint,
  store_id        bigint,
  priority        integer NOT NULL DEFAULT 1 CHECK (priority >= 1),
  valid_from      date NOT NULL DEFAULT CURRENT_DATE,
  valid_until     date,
  change_reason   varchar(160),
  assigned_by     varchar(120) NOT NULL,
  notes           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_supply_route_assignments PRIMARY KEY (id),
  CONSTRAINT ck_sra_validity CHECK (valid_until IS NULL OR valid_until >= valid_from),
  -- Scope must target either a region or a store (or both for store-in-region overrides).
  CONSTRAINT ck_sra_scope CHECK (region_id IS NOT NULL OR store_id IS NOT NULL),
  CONSTRAINT fk_sra_route  FOREIGN KEY (supply_route_id) REFERENCES supply_routes(id) ON DELETE CASCADE,
  CONSTRAINT fk_sra_region FOREIGN KEY (region_id)       REFERENCES regions(id)       ON DELETE SET NULL,
  CONSTRAINT fk_sra_store  FOREIGN KEY (store_id)        REFERENCES stores(id)        ON DELETE SET NULL
);

CREATE TABLE ingredient_supplier_refs (
  id              bigint GENERATED ALWAYS AS IDENTITY,
  ingredient_id   bigint NOT NULL,
  supply_route_id bigint NOT NULL,
  external_name   varchar(180) NOT NULL,
  external_code   varchar(80),
  purchase_unit   varchar(40) NOT NULL,
  units_per_pack  quantity_amount CHECK (units_per_pack IS NULL OR units_per_pack > 0),
  is_active       boolean NOT NULL DEFAULT true,
  notes           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_isr_refs PRIMARY KEY (id),
  CONSTRAINT uq_isr_refs UNIQUE (ingredient_id, supply_route_id),
  CONSTRAINT fk_isrref_ingredient FOREIGN KEY (ingredient_id)   REFERENCES ingredients(id)    ON DELETE CASCADE,
  CONSTRAINT fk_isrref_route      FOREIGN KEY (supply_route_id) REFERENCES supply_routes(id)  ON DELETE CASCADE
);
-- Partial unique: an external SKU must be unique within the rows that carry one.
CREATE UNIQUE INDEX uq_isr_external_code
  ON ingredient_supplier_refs (supply_route_id, external_code)
  WHERE external_code IS NOT NULL;

CREATE TABLE supplier_unit_conversions (
  id                bigint GENERATED ALWAYS AS IDENTITY,
  ingredient_ref_id bigint NOT NULL,
  recipe_unit_id    bigint NOT NULL,
  purchase_qty      quantity_amount NOT NULL CHECK (purchase_qty > 0),
  recipe_qty        quantity_amount NOT NULL CHECK (recipe_qty > 0),
  notes             text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_suc PRIMARY KEY (id),
  CONSTRAINT uq_suc UNIQUE (ingredient_ref_id, recipe_unit_id),
  CONSTRAINT fk_suc_ref  FOREIGN KEY (ingredient_ref_id) REFERENCES ingredient_supplier_refs(id) ON DELETE CASCADE,
  CONSTRAINT fk_suc_unit FOREIGN KEY (recipe_unit_id)    REFERENCES recipe_units(id)             ON DELETE RESTRICT
);

-- Effective-dated supplier prices. The EXCLUDE constraint forbids two
-- overlapping price windows for the same route (impossible with plain CHECKs).
CREATE TABLE supply_route_prices (
  id              bigint GENERATED ALWAYS AS IDENTITY,
  supply_route_id bigint NOT NULL,
  list_price      price_amount NOT NULL,
  qargo_price     price_amount NOT NULL,
  currency_code   char(3) NOT NULL,
  price_per_unit  varchar(40) NOT NULL,
  valid_from      date NOT NULL DEFAULT CURRENT_DATE,
  valid_until     date,
  source          varchar(120),
  created_by      varchar(120) NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_supply_route_prices PRIMARY KEY (id),
  CONSTRAINT ck_srp_validity CHECK (valid_until IS NULL OR valid_until >= valid_from),
  CONSTRAINT fk_srp_route    FOREIGN KEY (supply_route_id) REFERENCES supply_routes(id) ON DELETE CASCADE,
  CONSTRAINT fk_srp_currency FOREIGN KEY (currency_code)   REFERENCES currencies(code)  ON UPDATE CASCADE ON DELETE RESTRICT,
  CONSTRAINT no_overlap_srp EXCLUDE USING gist (
    supply_route_id WITH =,
    daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
  )
);

CREATE TABLE ingredient_availability (
  id              bigint GENERATED ALWAYS AS IDENTITY,
  ingredient_id   bigint NOT NULL,
  supply_route_id bigint,
  region_id       bigint,
  status          varchar(20) NOT NULL
    CHECK (status IN ('available', 'shortage', 'discontinued', 'seasonal')),
  expected_resume date,
  valid_from      date NOT NULL DEFAULT CURRENT_DATE,
  valid_until     date,
  reported_by     varchar(120),
  notes           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_ingredient_availability PRIMARY KEY (id),
  CONSTRAINT ck_ia_validity CHECK (valid_until IS NULL OR valid_until >= valid_from),
  CONSTRAINT fk_ia_ingredient FOREIGN KEY (ingredient_id)   REFERENCES ingredients(id)   ON DELETE CASCADE,
  CONSTRAINT fk_ia_route      FOREIGN KEY (supply_route_id) REFERENCES supply_routes(id) ON DELETE SET NULL,
  CONSTRAINT fk_ia_region     FOREIGN KEY (region_id)       REFERENCES regions(id)       ON DELETE SET NULL
);

CREATE TABLE store_supplier_history (
  id              bigint GENERATED ALWAYS AS IDENTITY,
  store_id        bigint NOT NULL,
  ingredient_id   bigint NOT NULL,
  supply_route_id bigint NOT NULL,
  valid_from      date NOT NULL,
  valid_until     date,
  change_reason   varchar(160),
  changed_by      varchar(120),
  notes           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_store_supplier_history PRIMARY KEY (id),
  CONSTRAINT ck_ssh_validity CHECK (valid_until IS NULL OR valid_until >= valid_from),
  CONSTRAINT fk_ssh_store      FOREIGN KEY (store_id)        REFERENCES stores(id)        ON DELETE CASCADE,
  CONSTRAINT fk_ssh_ingredient FOREIGN KEY (ingredient_id)  REFERENCES ingredients(id)   ON DELETE RESTRICT,
  CONSTRAINT fk_ssh_route      FOREIGN KEY (supply_route_id) REFERENCES supply_routes(id) ON DELETE RESTRICT
);

-- ===========================================================================
-- DOMAIN: Pricing, costs & history
-- ===========================================================================
CREATE TABLE store_ingredient_prices (
  id             bigint GENERATED ALWAYS AS IDENTITY,
  store_id       bigint NOT NULL,
  ingredient_id  bigint NOT NULL,
  local_price    price_amount,
  local_supplier varchar(160),
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_store_ingredient_prices PRIMARY KEY (id),
  CONSTRAINT uq_store_ingredient_prices UNIQUE (store_id, ingredient_id),  -- one current local price per pair
  CONSTRAINT fk_sip_store      FOREIGN KEY (store_id)      REFERENCES stores(id)      ON DELETE CASCADE,
  CONSTRAINT fk_sip_ingredient FOREIGN KEY (ingredient_id) REFERENCES ingredients(id) ON DELETE RESTRICT
);

-- product_pricing holds the CURRENT effective price per (product, size, store,
-- currency). It is intentionally a table, not a view, because is_manual_price /
-- markup_override store human overrides that a view cannot hold. store_id NULL
-- means the chain-wide default price.
CREATE TABLE product_pricing (
  id              bigint GENERATED ALWAYS AS IDENTITY,
  product_id      bigint NOT NULL,
  size_id         bigint NOT NULL,
  store_id        bigint,
  calculated_cost price_amount NOT NULL,
  markup_override pct_amount CHECK (markup_override IS NULL OR markup_override >= 0),
  final_price     price_amount NOT NULL,
  is_manual_price boolean NOT NULL DEFAULT false,
  effective_date  date NOT NULL DEFAULT CURRENT_DATE,
  currency_code   char(3) NOT NULL DEFAULT 'COP',
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_product_pricing PRIMARY KEY (id),
  CONSTRAINT fk_pp_product  FOREIGN KEY (product_id)    REFERENCES products(id)      ON DELETE CASCADE,
  CONSTRAINT fk_pp_size     FOREIGN KEY (size_id)       REFERENCES product_sizes(id) ON DELETE CASCADE,
  CONSTRAINT fk_pp_store    FOREIGN KEY (store_id)      REFERENCES stores(id)        ON DELETE CASCADE,
  CONSTRAINT fk_pp_currency FOREIGN KEY (currency_code) REFERENCES currencies(code)  ON UPDATE CASCADE ON DELETE RESTRICT
);
-- Uniqueness across a nullable store_id: COALESCE pins the "global" row (store NULL).
CREATE UNIQUE INDEX uq_product_pricing_current
  ON product_pricing (product_id, size_id, COALESCE(store_id, 0), currency_code);

-- product_price_history: append-only audit fact. Partitioned by month so the
-- table stays prune-able at millions of rows. NOTE: a partitioned table's PK
-- must include the partition key, hence PK (id, changed_at).
CREATE TABLE product_price_history (
  id            bigint GENERATED ALWAYS AS IDENTITY,
  product_id    bigint NOT NULL,
  size_id       bigint NOT NULL,
  store_id      bigint,
  cost          price_amount NOT NULL,
  price         price_amount NOT NULL,
  markup_used   pct_amount NOT NULL,
  currency_code char(3) NOT NULL DEFAULT 'COP',
  changed_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_product_price_history PRIMARY KEY (id, changed_at),
  CONSTRAINT fk_pph_product  FOREIGN KEY (product_id)    REFERENCES products(id)      ON DELETE RESTRICT,
  CONSTRAINT fk_pph_size     FOREIGN KEY (size_id)       REFERENCES product_sizes(id) ON DELETE RESTRICT,
  CONSTRAINT fk_pph_store    FOREIGN KEY (store_id)      REFERENCES stores(id)        ON DELETE SET NULL,
  CONSTRAINT fk_pph_currency FOREIGN KEY (currency_code) REFERENCES currencies(code)  ON UPDATE CASCADE ON DELETE RESTRICT
) PARTITION BY RANGE (changed_at);

CREATE TABLE product_price_history_2025 PARTITION OF product_price_history
  FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE product_price_history_2026 PARTITION OF product_price_history
  FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE product_price_history_default PARTITION OF product_price_history DEFAULT;

-- ingredient_price_history: same append-only + partitioning rationale.
CREATE TABLE ingredient_price_history (
  id            bigint GENERATED ALWAYS AS IDENTITY,
  ingredient_id bigint NOT NULL,
  price         price_amount NOT NULL,
  source        varchar(120),
  changed_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_ingredient_price_history PRIMARY KEY (id, changed_at),
  CONSTRAINT fk_iph_ingredient FOREIGN KEY (ingredient_id) REFERENCES ingredients(id) ON DELETE RESTRICT
) PARTITION BY RANGE (changed_at);

CREATE TABLE ingredient_price_history_2025 PARTITION OF ingredient_price_history
  FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE ingredient_price_history_2026 PARTITION OF ingredient_price_history
  FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE ingredient_price_history_default PARTITION OF ingredient_price_history DEFAULT;

-- recipe_cost_snapshots: immutable, point-in-time cost freeze for audit. The
-- JSONB detail is justified (a heterogeneous breakdown document, write-once /
-- read-rarely). Partitioned by calculated_at.
CREATE TABLE recipe_cost_snapshots (
  id              bigint GENERATED ALWAYS AS IDENTITY,
  product_id      bigint NOT NULL,
  store_id        bigint NOT NULL,
  base_cost       price_amount NOT NULL,
  effective_cost  price_amount NOT NULL,
  currency_code   char(3) NOT NULL,
  has_substitutes boolean NOT NULL DEFAULT false,
  snapshot_detail jsonb NOT NULL,
  triggered_by    varchar(120),
  calculated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_recipe_cost_snapshots PRIMARY KEY (id, calculated_at),
  CONSTRAINT fk_rcs_product  FOREIGN KEY (product_id)    REFERENCES products(id)     ON DELETE RESTRICT,
  CONSTRAINT fk_rcs_store    FOREIGN KEY (store_id)      REFERENCES stores(id)       ON DELETE RESTRICT,
  CONSTRAINT fk_rcs_currency FOREIGN KEY (currency_code) REFERENCES currencies(code) ON UPDATE CASCADE ON DELETE RESTRICT
) PARTITION BY RANGE (calculated_at);

CREATE TABLE recipe_cost_snapshots_2025 PARTITION OF recipe_cost_snapshots
  FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE recipe_cost_snapshots_2026 PARTITION OF recipe_cost_snapshots
  FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE recipe_cost_snapshots_default PARTITION OF recipe_cost_snapshots DEFAULT;

-- ===========================================================================
-- DOMAIN: Competitive intelligence
-- ===========================================================================
-- competitor_products is a scrape log (one row per scrape). Append-mostly and
-- high-growth -> partitioned by scraped_at.
CREATE TABLE competitor_products (
  id               bigint GENERATED ALWAYS AS IDENTITY,
  competitor_id    bigint NOT NULL,
  product_name     varchar(180),
  category         varchar(80),
  size_description varchar(80),
  price            price_amount,
  source_url       text,
  scraped_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_competitor_products PRIMARY KEY (id, scraped_at),
  CONSTRAINT fk_cp_competitor FOREIGN KEY (competitor_id) REFERENCES competitors(id) ON DELETE CASCADE
) PARTITION BY RANGE (scraped_at);

CREATE TABLE competitor_products_2025 PARTITION OF competitor_products
  FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE competitor_products_2026 PARTITION OF competitor_products
  FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE competitor_products_default PARTITION OF competitor_products DEFAULT;

-- product_competitor_matches references a partitioned table. A FK to a
-- partitioned parent requires referencing its full PK, so the match carries the
-- competitor_product_scraped_at column as part of the composite FK.
CREATE TABLE product_competitor_matches (
  id                           bigint GENERATED ALWAYS AS IDENTITY,
  our_product_id               bigint NOT NULL,
  our_size_id                  bigint NOT NULL,
  competitor_product_id        bigint NOT NULL,
  competitor_product_scraped_at timestamptz NOT NULL,
  matched_by                   varchar(120),
  matched_at                   timestamptz NOT NULL DEFAULT now(),
  notes                        text,
  CONSTRAINT pk_product_competitor_matches PRIMARY KEY (id),
  CONSTRAINT uq_product_competitor_matches UNIQUE (our_product_id, our_size_id, competitor_product_id),
  CONSTRAINT fk_pcm_product FOREIGN KEY (our_product_id) REFERENCES products(id)      ON DELETE CASCADE,
  CONSTRAINT fk_pcm_size    FOREIGN KEY (our_size_id)    REFERENCES product_sizes(id) ON DELETE CASCADE,
  CONSTRAINT fk_pcm_competitor_product FOREIGN KEY (competitor_product_id, competitor_product_scraped_at)
    REFERENCES competitor_products (id, scraped_at) ON DELETE CASCADE
);

CREATE TABLE store_products (
  id                 bigint GENERATED ALWAYS AS IDENTITY,
  store_id           bigint NOT NULL,
  product_id         bigint NOT NULL,
  is_available       boolean NOT NULL DEFAULT true,
  seasonal_start_date date,
  seasonal_end_date  date,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_store_products PRIMARY KEY (id),
  CONSTRAINT uq_store_products UNIQUE (store_id, product_id),
  CONSTRAINT fk_sp_store   FOREIGN KEY (store_id)   REFERENCES stores(id)   ON DELETE CASCADE,
  CONSTRAINT fk_sp_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

-- ===========================================================================
-- DOMAIN: Alembic (managed externally — shown for completeness only)
-- ===========================================================================
-- DO NOT recreate this on an existing database; Alembic owns its state here.
CREATE TABLE alembic_version (
  version_num varchar(32) NOT NULL,
  CONSTRAINT pk_alembic_version PRIMARY KEY (version_num)
);

-- ===========================================================================
-- INDEXES — every FK gets a covering index; composites match access patterns.
-- PostgreSQL indexes PK/UNIQUE automatically but NOT foreign keys.
-- ===========================================================================
-- Catalog
CREATE INDEX idx_products_category            ON products (category);
CREATE INDEX idx_product_sizes_product        ON product_sizes (product_id);
CREATE UNIQUE INDEX uq_product_sizes_default  ON product_sizes (product_id) WHERE is_default; -- one default size per product
CREATE INDEX idx_recipe_ingredients_ingredient ON recipe_ingredients (ingredient_id);
CREATE INDEX idx_recipe_ingredients_unit      ON recipe_ingredients (recipe_unit_id);
CREATE INDEX idx_recipe_sub_recipes_sub       ON recipe_sub_recipes (sub_recipe_id);
CREATE INDEX idx_size_packaging_ingredient    ON size_packaging (packaging_ingredient_id);
CREATE INDEX idx_mie_ingredient               ON modifier_ingredient_effects (ingredient_id);
CREATE INDEX idx_pmc_modifier                 ON product_modifier_costs (modifier_id);

-- Ingredient catalog
CREATE INDEX idx_iruc_unit                    ON ingredient_recipe_unit_conversions (recipe_unit_id);
CREATE INDEX idx_isub_substitute              ON ingredient_substitutes (substitute_ingredient_id);
CREATE INDEX idx_isub_unit                    ON ingredient_substitutes (recipe_unit_id);
CREATE INDEX idx_isr_regions_region           ON ingredient_substitute_regions (region_id);

-- Supply chain
CREATE INDEX idx_sr_ingredient                ON supply_routes (ingredient_id);
CREATE INDEX idx_sr_manufacturer              ON supply_routes (manufacturer_id);
CREATE INDEX idx_sr_distributor               ON supply_routes (distributor_id);
CREATE INDEX idx_sra_route                    ON supply_route_assignments (supply_route_id);
CREATE INDEX idx_sra_region                   ON supply_route_assignments (region_id);
CREATE INDEX idx_sra_store                    ON supply_route_assignments (store_id);
CREATE INDEX idx_isrref_route                 ON ingredient_supplier_refs (supply_route_id);
CREATE INDEX idx_suc_unit                     ON supplier_unit_conversions (recipe_unit_id);
CREATE INDEX idx_srp_route_validfrom          ON supply_route_prices (supply_route_id, valid_from DESC);
CREATE INDEX idx_srp_currency                 ON supply_route_prices (currency_code);
CREATE INDEX idx_ia_ingredient                ON ingredient_availability (ingredient_id);
CREATE INDEX idx_ia_route                     ON ingredient_availability (supply_route_id);
CREATE INDEX idx_ia_region                    ON ingredient_availability (region_id);
CREATE INDEX idx_ssh_store_ing_validfrom      ON store_supplier_history (store_id, ingredient_id, valid_from DESC);
CREATE INDEX idx_ssh_route                    ON store_supplier_history (supply_route_id);

-- Pricing & history
CREATE INDEX idx_sip_ingredient               ON store_ingredient_prices (ingredient_id);
CREATE INDEX idx_pp_size                      ON product_pricing (size_id);
CREATE INDEX idx_pp_store                     ON product_pricing (store_id);
CREATE INDEX idx_pph_product_size_changed     ON product_price_history (product_id, size_id, changed_at DESC);
CREATE INDEX idx_iph_ingredient_changed       ON ingredient_price_history (ingredient_id, changed_at DESC);
CREATE INDEX idx_rcs_product_store_calc        ON recipe_cost_snapshots (product_id, store_id, calculated_at DESC);

-- Competitive intel & stores
CREATE INDEX idx_cp_competitor_scraped        ON competitor_products (competitor_id, scraped_at DESC);
CREATE INDEX idx_pcm_competitor_product       ON product_competitor_matches (competitor_product_id, competitor_product_scraped_at);
CREATE INDEX idx_pcm_size                     ON product_competitor_matches (our_size_id);
CREATE INDEX idx_sp_product                   ON store_products (product_id);

-- Optional accelerators (enable when the matching access pattern is confirmed):
-- Fuzzy name search:
--   CREATE INDEX idx_ingredients_name_trgm ON ingredients USING gin (name gin_trgm_ops);
--   CREATE INDEX idx_products_name_trgm    ON products    USING gin (name gin_trgm_ops);
-- JSONB containment queries (@>, ->>):
--   CREATE INDEX idx_supply_routes_metadata ON supply_routes USING gin (metadata);
--   CREATE INDEX idx_rcs_detail             ON recipe_cost_snapshots USING gin (snapshot_detail);
-- "Active only" partial indexes for very large dimensions:
--   CREATE INDEX idx_ingredients_active ON ingredients (id) WHERE is_active;

-- ===========================================================================
-- TRIGGERS — auto-maintain updated_at on every mutable table
-- ===========================================================================
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'categories','regions','recipe_units','ingredients','manufacturers','distributors',
    'competitors','modifiers','stores','products','category_margins','product_sizes',
    'recipe_ingredients','recipe_sub_recipes','size_packaging','modifier_ingredient_effects',
    'ingredient_recipe_unit_conversions','ingredient_substitutes','supply_routes',
    'ingredient_supplier_refs','store_ingredient_prices','product_pricing','store_products'
  ]
  LOOP
    EXECUTE format(
      'CREATE TRIGGER trg_%1$s_set_updated_at
         BEFORE UPDATE ON %1$s
         FOR EACH ROW EXECUTE FUNCTION set_updated_at();', t);
  END LOOP;
END;
$$;

-- ===========================================================================
-- VIEWS — derived data that should be computed, not stored
-- ===========================================================================

-- Latest known price per ingredient (from the append-only history).
CREATE VIEW v_current_ingredient_price AS
SELECT DISTINCT ON (iph.ingredient_id)
       iph.ingredient_id,
       iph.price,
       iph.source,
       iph.changed_at
FROM ingredient_price_history iph
ORDER BY iph.ingredient_id, iph.changed_at DESC;

-- Live modifier cost impact (replaces persisting product_modifier_costs).
CREATE VIEW v_product_modifier_cost AS
SELECT mie.modifier_id,
       cip.ingredient_id,
       SUM(mie.quantity_change * cip.price) AS cost_impact
FROM modifier_ingredient_effects mie
JOIN v_current_ingredient_price cip ON cip.ingredient_id = mie.ingredient_id
GROUP BY mie.modifier_id, cip.ingredient_id;

-- Currently active ingredient availability records.
CREATE VIEW v_current_ingredient_availability AS
SELECT ia.*
FROM ingredient_availability ia
WHERE ia.valid_from <= CURRENT_DATE
  AND (ia.valid_until IS NULL OR ia.valid_until >= CURRENT_DATE);

-- Effective price (manual override wins over the computed/cached value).
CREATE VIEW v_product_effective_price AS
SELECT pp.product_id,
       pp.size_id,
       pp.store_id,
       pp.currency_code,
       pp.final_price,
       pp.is_manual_price,
       pp.calculated_cost,
       pp.effective_date
FROM product_pricing pp;

COMMIT;
