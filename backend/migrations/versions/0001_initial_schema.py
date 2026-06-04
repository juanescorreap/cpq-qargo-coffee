"""initial greenfield schema (refactored target state)

Single migration that materializes the validated target DDL from
``files/schema_refactorizado.sql``. The whole schema is created here via
``op.execute`` because it relies on PostgreSQL features Alembic autogenerate
does not handle: DOMAINs, GENERATED ALWAYS AS IDENTITY, partitioned tables,
EXCLUDE constraints, partial unique indexes, trigger functions and views.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-04
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Full target DDL. Differences vs files/schema_refactorizado.sql:
#   - no BEGIN/COMMIT (Alembic wraps the migration in a transaction)
#   - the alembic_version table is NOT created here (Alembic owns it)
# ---------------------------------------------------------------------------
SCHEMA_SQL = r"""
-- 0. Extensions
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 1. Domains
CREATE DOMAIN price_amount    AS numeric(14, 4) CHECK (VALUE >= 0);
CREATE DOMAIN quantity_amount AS numeric(14, 6) CHECK (VALUE >= 0);
CREATE DOMAIN pct_amount      AS numeric(6, 3);
CREATE DOMAIN iso_country     AS char(2) CHECK (VALUE ~ '^[A-Z]{2}$');

-- 2. Shared trigger function
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

-- Reference / lookup tables
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

-- Stores & geography
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

-- Product & recipe catalog
CREATE TABLE products (
  id                    bigint GENERATED ALWAYS AS IDENTITY,
  name                  varchar(180) NOT NULL,
  category              varchar(80),
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
  quantity_change numeric(14, 6) NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_mie_quantity_change_nonzero CHECK (quantity_change <> 0),
  CONSTRAINT pk_modifier_ingredient_effects PRIMARY KEY (id),
  CONSTRAINT uq_modifier_ingredient_effects UNIQUE (modifier_id, ingredient_id),
  CONSTRAINT fk_mie_modifier   FOREIGN KEY (modifier_id)   REFERENCES modifiers(id)   ON DELETE CASCADE,
  CONSTRAINT fk_mie_ingredient FOREIGN KEY (ingredient_id) REFERENCES ingredients(id) ON DELETE RESTRICT
);

CREATE TABLE product_modifier_costs (
  id            bigint GENERATED ALWAYS AS IDENTITY,
  product_id    bigint NOT NULL,
  modifier_id   bigint NOT NULL,
  cost_impact   numeric(14, 4) NOT NULL,
  calculated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_product_modifier_costs PRIMARY KEY (id),
  CONSTRAINT uq_product_modifier_costs UNIQUE (product_id, modifier_id),
  CONSTRAINT fk_pmc_product  FOREIGN KEY (product_id)  REFERENCES products(id)  ON DELETE CASCADE,
  CONSTRAINT fk_pmc_modifier FOREIGN KEY (modifier_id) REFERENCES modifiers(id) ON DELETE CASCADE
);

-- Ingredient catalog extensions
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

CREATE TABLE ingredient_substitute_regions (
  substitute_id bigint NOT NULL,
  region_id     bigint NOT NULL,
  CONSTRAINT pk_isub_regions PRIMARY KEY (substitute_id, region_id),
  CONSTRAINT fk_isr_substitute FOREIGN KEY (substitute_id) REFERENCES ingredient_substitutes(id) ON DELETE CASCADE,
  CONSTRAINT fk_isr_region     FOREIGN KEY (region_id)     REFERENCES regions(id)                 ON DELETE CASCADE
);

-- Supply chain
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
  CONSTRAINT ck_srp_qargo_lte_list CHECK (qargo_price <= list_price),
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
  CONSTRAINT ck_ia_resume_only_for_shortage CHECK (expected_resume IS NULL OR status = 'shortage'),
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

-- Pricing, costs & history
CREATE TABLE store_ingredient_prices (
  id             bigint GENERATED ALWAYS AS IDENTITY,
  store_id       bigint NOT NULL,
  ingredient_id  bigint NOT NULL,
  local_price    price_amount,
  local_supplier varchar(160),
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_store_ingredient_prices PRIMARY KEY (id),
  CONSTRAINT uq_store_ingredient_prices UNIQUE (store_id, ingredient_id),
  CONSTRAINT fk_sip_store      FOREIGN KEY (store_id)      REFERENCES stores(id)      ON DELETE CASCADE,
  CONSTRAINT fk_sip_ingredient FOREIGN KEY (ingredient_id) REFERENCES ingredients(id) ON DELETE RESTRICT
);

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
CREATE UNIQUE INDEX uq_product_pricing_current
  ON product_pricing (product_id, size_id, COALESCE(store_id, 0), currency_code);

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

-- Competitive intelligence
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

-- INDEXES
CREATE INDEX idx_products_category            ON products (category);
CREATE INDEX idx_product_sizes_product        ON product_sizes (product_id);
CREATE UNIQUE INDEX uq_product_sizes_default  ON product_sizes (product_id) WHERE is_default;
CREATE INDEX idx_recipe_ingredients_ingredient ON recipe_ingredients (ingredient_id);
CREATE INDEX idx_recipe_ingredients_unit      ON recipe_ingredients (recipe_unit_id);
CREATE INDEX idx_recipe_sub_recipes_sub       ON recipe_sub_recipes (sub_recipe_id);
CREATE INDEX idx_size_packaging_ingredient    ON size_packaging (packaging_ingredient_id);
CREATE INDEX idx_mie_ingredient               ON modifier_ingredient_effects (ingredient_id);
CREATE INDEX idx_pmc_modifier                 ON product_modifier_costs (modifier_id);
CREATE INDEX idx_iruc_unit                    ON ingredient_recipe_unit_conversions (recipe_unit_id);
CREATE INDEX idx_isub_substitute              ON ingredient_substitutes (substitute_ingredient_id);
CREATE INDEX idx_isub_unit                    ON ingredient_substitutes (recipe_unit_id);
CREATE INDEX idx_isr_regions_region           ON ingredient_substitute_regions (region_id);
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
CREATE INDEX idx_sip_ingredient               ON store_ingredient_prices (ingredient_id);
CREATE INDEX idx_pp_size                      ON product_pricing (size_id);
CREATE INDEX idx_pp_store                     ON product_pricing (store_id);
CREATE INDEX idx_pph_product_size_changed     ON product_price_history (product_id, size_id, changed_at DESC);
CREATE INDEX idx_iph_ingredient_changed       ON ingredient_price_history (ingredient_id, changed_at DESC);
CREATE INDEX idx_rcs_product_store_calc       ON recipe_cost_snapshots (product_id, store_id, calculated_at DESC);
CREATE INDEX idx_cp_competitor_scraped        ON competitor_products (competitor_id, scraped_at DESC);
CREATE INDEX idx_pcm_competitor_product       ON product_competitor_matches (competitor_product_id, competitor_product_scraped_at);
CREATE INDEX idx_pcm_size                     ON product_competitor_matches (our_size_id);
CREATE INDEX idx_sp_product                   ON store_products (product_id);

-- TRIGGERS: auto-maintain updated_at
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

-- VIEWS
CREATE VIEW v_current_ingredient_price AS
SELECT DISTINCT ON (iph.ingredient_id)
       iph.ingredient_id,
       iph.price,
       iph.source,
       iph.changed_at
FROM ingredient_price_history iph
ORDER BY iph.ingredient_id, iph.changed_at DESC;

CREATE VIEW v_product_modifier_cost AS
SELECT mie.modifier_id,
       cip.ingredient_id,
       SUM(mie.quantity_change * cip.price) AS cost_impact
FROM modifier_ingredient_effects mie
JOIN v_current_ingredient_price cip ON cip.ingredient_id = mie.ingredient_id
GROUP BY mie.modifier_id, cip.ingredient_id;

CREATE VIEW v_current_ingredient_availability AS
SELECT ia.*
FROM ingredient_availability ia
WHERE ia.valid_from <= CURRENT_DATE
  AND (ia.valid_until IS NULL OR ia.valid_until >= CURRENT_DATE);

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
"""


def upgrade() -> None:
    op.execute(SCHEMA_SQL)


def downgrade() -> None:
    op.execute("DROP SCHEMA public CASCADE")
    op.execute("CREATE SCHEMA public")
    op.execute("GRANT ALL ON SCHEMA public TO postgres")
    op.execute("GRANT ALL ON SCHEMA public TO public")
