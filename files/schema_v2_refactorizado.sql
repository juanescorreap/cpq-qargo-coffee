-- ============================================================================
-- Qargo Coffee — Esquema v2 (propuesta del análisis Principal Engineer)
-- PostgreSQL 15+
-- ----------------------------------------------------------------------------
-- Parte del esquema v1 ya implementado (files/schema_refactorizado.sql +
-- migraciones 0001-0004) e incorpora los hallazgos del análisis:
--
--   C1  ingredient_substitutes: EXCLUDE temporal en vez de UNIQUE(orig,sub)
--   C2  v_current_ingredient_price -> MATERIALIZED VIEW + índice único
--   C3  ingredient_availability: CHECK de ámbito + FKs ON DELETE CASCADE
--   C4  product_modifier_costs -> MATERIALIZED VIEW (elimina tabla derivada)
--   A1  precedencia de precio de ingrediente: función única fn_ingredient_unit_cost
--   A2  competidores: catálogo estable + log de observaciones particionado
--   A3  particionamiento: helper de creación + nota pg_partman
--   M1  product_pricing: UNIQUE NULLS NOT DISTINCT en vez de COALESCE(store,0)
--   M3  recipe_ingredients.recipe_unit_id -> ON DELETE RESTRICT
--
-- También conserva las restauraciones P7 (migración 0003) y fn_resolve_supply_route
-- (migración 0002).
--
-- Orden de creación respeta dependencias de FK. Ejecutable top-to-bottom en BD
-- vacía dentro de una transacción.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 0. Extensiones
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS btree_gist;  -- EXCLUDE con (=, daterange)
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- búsqueda difusa por nombre

-- ---------------------------------------------------------------------------
-- 1. Dominios — única fuente de verdad de la semántica numérica
-- ---------------------------------------------------------------------------
CREATE DOMAIN price_amount    AS numeric(14, 4) CHECK (VALUE >= 0);
CREATE DOMAIN quantity_amount AS numeric(14, 6) CHECK (VALUE >= 0);
CREATE DOMAIN pct_amount      AS numeric(6, 3);
CREATE DOMAIN iso_country     AS char(2) CHECK (VALUE ~ '^[A-Z]{2}$');

-- ---------------------------------------------------------------------------
-- 2. Función compartida updated_at
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- 3. Helper de particionamiento (A3)
-- Crea una partición anual si no existe. En producción, programar para crear
-- el año siguiente con antelación (pg_cron) o usar pg_partman directamente.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ensure_yearly_partition(
    p_parent regclass, p_year int
) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    part_name text := format('%s_%s', p_parent::text, p_year);
BEGIN
    IF to_regclass(part_name) IS NULL THEN
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF %s FOR VALUES FROM (%L) TO (%L)',
            part_name, p_parent::text,
            format('%s-01-01', p_year), format('%s-01-01', p_year + 1)
        );
    END IF;
END;
$$;

-- ===========================================================================
-- DOMINIO: Referencia / lookup
-- ===========================================================================
CREATE TABLE currencies (
  code        char(3) NOT NULL,
  name        varchar(64) NOT NULL,
  minor_unit  smallint NOT NULL DEFAULT 2 CHECK (minor_unit BETWEEN 0 AND 4),
  is_active   boolean NOT NULL DEFAULT true,
  CONSTRAINT pk_currencies PRIMARY KEY (code),
  CONSTRAINT ck_currencies_code CHECK (code ~ '^[A-Z]{3}$')
);
INSERT INTO currencies (code, name, minor_unit) VALUES
  ('COP','Colombian Peso',0), ('USD','US Dollar',2), ('EUR','Euro',2);

-- Slug admite '_' y '-' (M en análisis: convención real de la app usa '_').
CREATE TABLE categories (
  slug          varchar(80) NOT NULL,
  display_name  varchar(160),
  notes         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_categories PRIMARY KEY (slug),
  CONSTRAINT ck_categories_slug_format CHECK (slug ~ '^[a-z0-9]+([_-][a-z0-9]+)*$')
);

CREATE TABLE regions (
  id           bigint GENERATED ALWAYS AS IDENTITY,
  name         varchar(120) NOT NULL,
  code         varchar(40)  NOT NULL,
  country_code iso_country  NOT NULL DEFAULT 'CO',
  is_active    boolean NOT NULL DEFAULT true,
  metadata     jsonb,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_regions PRIMARY KEY (id),
  CONSTRAINT uq_regions_code UNIQUE (code)
);

CREATE TABLE recipe_units (
  id          bigint GENERATED ALWAYS AS IDENTITY,
  name        varchar(60) NOT NULL,
  category    varchar(60),
  description text,
  is_active   boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_recipe_units PRIMARY KEY (id),
  CONSTRAINT uq_recipe_units_name UNIQUE (name)
);

CREATE TABLE ingredients (
  id                bigint GENERATED ALWAYS AS IDENTITY,
  name              varchar(180) NOT NULL,
  category          varchar(80),
  purchase_unit     varchar(40),
  purchase_price    price_amount,
  usage_unit        varchar(40),
  conversion_factor quantity_amount CHECK (conversion_factor IS NULL OR conversion_factor > 0),
  yield_percentage  pct_amount CHECK (yield_percentage IS NULL OR yield_percentage BETWEEN 0 AND 100),
  canonical_unit    varchar(40),
  -- C2: precio actual denormalizado, mantenido por trigger desde el historial.
  -- Lectura O(1) para el motor de costos; el historial sigue siendo la verdad.
  current_price     price_amount,
  source_url        text,
  last_scraped      timestamptz,
  is_active         boolean NOT NULL DEFAULT true,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_ingredients PRIMARY KEY (id)
);

CREATE TABLE manufacturers (
  id           bigint GENERATED ALWAYS AS IDENTITY,
  name         varchar(160) NOT NULL,
  country_code iso_country  NOT NULL DEFAULT 'CO',
  tax_id       varchar(40),
  website      text,
  is_active    boolean NOT NULL DEFAULT true,
  metadata     jsonb,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_manufacturers PRIMARY KEY (id)
);

CREATE TABLE distributors (
  id            bigint GENERATED ALWAYS AS IDENTITY,
  name          varchar(160) NOT NULL,
  country_code  iso_country  NOT NULL DEFAULT 'CO',
  tax_id        varchar(40),
  contact_email varchar(160),
  contact_phone varchar(40),
  is_active     boolean NOT NULL DEFAULT true,
  metadata      jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_distributors PRIMARY KEY (id),
  CONSTRAINT ck_distributors_email CHECK (contact_email IS NULL OR contact_email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$')
);

CREATE TABLE competitors (
  id          bigint GENERATED ALWAYS AS IDENTITY,
  name        varchar(160) NOT NULL,
  website_url text,
  is_active   boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_competitors PRIMARY KEY (id)
);

CREATE TABLE modifiers (
  id         bigint GENERATED ALWAYS AS IDENTITY,
  name       varchar(120) NOT NULL,
  type       varchar(60),
  is_active  boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_modifiers PRIMARY KEY (id)
);

-- ===========================================================================
-- DOMINIO: Tiendas
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
-- DOMINIO: Catálogo producto / receta
-- ===========================================================================
CREATE TABLE products (
  id                    bigint GENERATED ALWAYS AS IDENTITY,
  name                  varchar(180) NOT NULL,
  category              varchar(80),
  base_size_oz          numeric(10,3) CHECK (base_size_oz IS NULL OR base_size_oz > 0),
  prep_time_minutes     numeric(8,2)  CHECK (prep_time_minutes IS NULL OR prep_time_minutes >= 0),
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
  volume_oz    numeric(10,3) CHECK (volume_oz IS NULL OR volume_oz > 0),
  scale_factor quantity_amount CHECK (scale_factor IS NULL OR scale_factor > 0),
  is_default   boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_product_sizes PRIMARY KEY (id),
  CONSTRAINT uq_product_sizes_name UNIQUE (product_id, size_name),
  CONSTRAINT fk_product_sizes_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE recipe_ingredients (
  id                 bigint GENERATED ALWAYS AS IDENTITY,
  product_id         bigint NOT NULL,
  ingredient_id      bigint NOT NULL,
  quantity           quantity_amount NOT NULL CHECK (quantity > 0),
  recipe_unit_id     bigint,
  scales_with_size   boolean NOT NULL DEFAULT true,
  process_yield_loss pct_amount CHECK (process_yield_loss IS NULL OR process_yield_loss BETWEEN 0 AND 100),
  notes              text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_recipe_ingredients PRIMARY KEY (id),
  CONSTRAINT uq_recipe_ingredients UNIQUE (product_id, ingredient_id),
  CONSTRAINT fk_recipe_ingredients_product    FOREIGN KEY (product_id)     REFERENCES products(id)     ON DELETE CASCADE,
  CONSTRAINT fk_recipe_ingredients_ingredient FOREIGN KEY (ingredient_id)  REFERENCES ingredients(id)  ON DELETE RESTRICT,
  -- M3: RESTRICT, no SET NULL — borrar una unidad no debe alterar la receta en silencio.
  CONSTRAINT fk_recipe_ingredients_unit       FOREIGN KEY (recipe_unit_id) REFERENCES recipe_units(id) ON DELETE RESTRICT
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
  CONSTRAINT fk_size_packaging_size       FOREIGN KEY (size_id)                 REFERENCES product_sizes(id) ON DELETE CASCADE,
  CONSTRAINT fk_size_packaging_ingredient FOREIGN KEY (packaging_ingredient_id) REFERENCES ingredients(id)    ON DELETE RESTRICT
);

CREATE TABLE modifier_ingredient_effects (
  id              bigint GENERATED ALWAYS AS IDENTITY,
  modifier_id     bigint NOT NULL,
  ingredient_id   bigint NOT NULL,
  quantity_change numeric(14,6) NOT NULL,  -- firmado: +añade / -quita
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_mie_quantity_change_nonzero CHECK (quantity_change <> 0),
  CONSTRAINT pk_modifier_ingredient_effects PRIMARY KEY (id),
  CONSTRAINT uq_modifier_ingredient_effects UNIQUE (modifier_id, ingredient_id),
  CONSTRAINT fk_mie_modifier   FOREIGN KEY (modifier_id)   REFERENCES modifiers(id)   ON DELETE CASCADE,
  CONSTRAINT fk_mie_ingredient FOREIGN KEY (ingredient_id) REFERENCES ingredients(id) ON DELETE RESTRICT
);
-- C4: product_modifier_costs ELIMINADA. El costo de modificador es derivado;
-- ahora vive en la matview mv_product_modifier_cost (más abajo).

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
    CHECK (activation_condition IN ('shortage','unavailable','always')),
  quantity_ratio           quantity_amount NOT NULL DEFAULT 1.0 CHECK (quantity_ratio > 0),
  recipe_unit_id           bigint,
  cost_impact_pct          pct_amount,
  valid_from               date NOT NULL DEFAULT CURRENT_DATE,
  valid_until              date,
  notes                    text,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_ingredient_substitutes PRIMARY KEY (id),
  CONSTRAINT ck_ingredient_substitutes_no_self CHECK (original_ingredient_id <> substitute_ingredient_id),
  CONSTRAINT ck_ingredient_substitutes_validity CHECK (valid_until IS NULL OR valid_until >= valid_from),
  -- C1: EXCLUDE temporal en vez de UNIQUE(orig,sub). Permite re-aprobar el mismo
  -- par en periodos distintos (historial) y prohíbe solapamiento de vigencias.
  CONSTRAINT no_overlap_isub EXCLUDE USING gist (
    original_ingredient_id   WITH =,
    substitute_ingredient_id WITH =,
    daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
  ),
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

-- ===========================================================================
-- DOMINIO: Cadena de suministro
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
  CONSTRAINT ck_supply_routes_endpoint CHECK (manufacturer_id IS NOT NULL OR distributor_id IS NOT NULL),
  -- P7 (migración 0003): compra directa no lleva distribuidor.
  CONSTRAINT ck_supply_routes_direct_no_distributor CHECK (NOT (is_direct = true AND distributor_id IS NOT NULL)),
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
  -- P7 (migración 0003): sin solapamiento de prioridad por ámbito.
  CONSTRAINT no_overlap_sra_store EXCLUDE USING gist (
    store_id WITH =, priority WITH =,
    daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
  ) WHERE (store_id IS NOT NULL),
  CONSTRAINT no_overlap_sra_region EXCLUDE USING gist (
    region_id WITH =, priority WITH =,
    daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
  ) WHERE (region_id IS NOT NULL),
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
  CONSTRAINT fk_isrref_ingredient FOREIGN KEY (ingredient_id)   REFERENCES ingredients(id)   ON DELETE CASCADE,
  CONSTRAINT fk_isrref_route      FOREIGN KEY (supply_route_id) REFERENCES supply_routes(id) ON DELETE CASCADE
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

-- C3: CHECK de ámbito + FKs CASCADE (no SET NULL) para no dejar observaciones huérfanas.
CREATE TABLE ingredient_availability (
  id              bigint GENERATED ALWAYS AS IDENTITY,
  ingredient_id   bigint NOT NULL,
  supply_route_id bigint,
  region_id       bigint,
  status          varchar(20) NOT NULL
    CHECK (status IN ('available','shortage','discontinued','seasonal')),
  expected_resume date,
  valid_from      date NOT NULL DEFAULT CURRENT_DATE,
  valid_until     date,
  reported_by     varchar(120),
  notes           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_ingredient_availability PRIMARY KEY (id),
  CONSTRAINT ck_ia_validity CHECK (valid_until IS NULL OR valid_until >= valid_from),
  CONSTRAINT ck_ia_scope CHECK (supply_route_id IS NOT NULL OR region_id IS NOT NULL),
  CONSTRAINT ck_ia_resume_only_for_shortage CHECK (expected_resume IS NULL OR status = 'shortage'),
  CONSTRAINT fk_ia_ingredient FOREIGN KEY (ingredient_id)   REFERENCES ingredients(id)   ON DELETE CASCADE,
  CONSTRAINT fk_ia_route      FOREIGN KEY (supply_route_id) REFERENCES supply_routes(id) ON DELETE CASCADE,
  CONSTRAINT fk_ia_region     FOREIGN KEY (region_id)       REFERENCES regions(id)       ON DELETE CASCADE
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
  -- P7 (migración 0003): una ruta activa por store+ingrediente a la vez.
  CONSTRAINT no_overlap_ssh EXCLUDE USING gist (
    store_id WITH =, ingredient_id WITH =,
    daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
  ),
  CONSTRAINT fk_ssh_store      FOREIGN KEY (store_id)        REFERENCES stores(id)        ON DELETE CASCADE,
  CONSTRAINT fk_ssh_ingredient FOREIGN KEY (ingredient_id)  REFERENCES ingredients(id)   ON DELETE RESTRICT,
  CONSTRAINT fk_ssh_route      FOREIGN KEY (supply_route_id) REFERENCES supply_routes(id) ON DELETE RESTRICT
);

-- ===========================================================================
-- DOMINIO: Precios, costos e historial
-- ===========================================================================
-- A1: precio local con TEMPORALIDAD (antes sin historial). La precedencia de
-- precio (local vs ruta) la resuelve fn_ingredient_unit_cost (más abajo).
CREATE TABLE store_ingredient_prices (
  id             bigint GENERATED ALWAYS AS IDENTITY,
  store_id       bigint NOT NULL,
  ingredient_id  bigint NOT NULL,
  local_price    price_amount NOT NULL,
  currency_code  char(3) NOT NULL DEFAULT 'COP',
  local_supplier varchar(160),
  valid_from     date NOT NULL DEFAULT CURRENT_DATE,
  valid_until    date,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_store_ingredient_prices PRIMARY KEY (id),
  CONSTRAINT ck_sip_validity CHECK (valid_until IS NULL OR valid_until >= valid_from),
  CONSTRAINT no_overlap_sip EXCLUDE USING gist (
    store_id WITH =, ingredient_id WITH =,
    daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
  ),
  CONSTRAINT fk_sip_store      FOREIGN KEY (store_id)      REFERENCES stores(id)       ON DELETE CASCADE,
  CONSTRAINT fk_sip_ingredient FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)  ON DELETE RESTRICT,
  CONSTRAINT fk_sip_currency   FOREIGN KEY (currency_code)  REFERENCES currencies(code) ON UPDATE CASCADE ON DELETE RESTRICT
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
-- M1: NULLS NOT DISTINCT (PG15+) en vez del truco COALESCE(store_id,0).
-- store_id NULL = precio global; trata todos los NULL como iguales.
CREATE UNIQUE INDEX uq_product_pricing_current
  ON product_pricing (product_id, size_id, store_id, currency_code) NULLS NOT DISTINCT;

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
SELECT ensure_yearly_partition('product_price_history', 2025);
SELECT ensure_yearly_partition('product_price_history', 2026);
SELECT ensure_yearly_partition('product_price_history', 2027);
-- A3: sin partición DEFAULT — forzar que exista la partición del año (job pg_cron).

CREATE TABLE ingredient_price_history (
  id            bigint GENERATED ALWAYS AS IDENTITY,
  ingredient_id bigint NOT NULL,
  price         price_amount NOT NULL,
  source        varchar(120),
  changed_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_ingredient_price_history PRIMARY KEY (id, changed_at),
  CONSTRAINT fk_iph_ingredient FOREIGN KEY (ingredient_id) REFERENCES ingredients(id) ON DELETE RESTRICT
) PARTITION BY RANGE (changed_at);
SELECT ensure_yearly_partition('ingredient_price_history', 2025);
SELECT ensure_yearly_partition('ingredient_price_history', 2026);
SELECT ensure_yearly_partition('ingredient_price_history', 2027);

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
SELECT ensure_yearly_partition('recipe_cost_snapshots', 2025);
SELECT ensure_yearly_partition('recipe_cost_snapshots', 2026);
SELECT ensure_yearly_partition('recipe_cost_snapshots', 2027);

-- ===========================================================================
-- DOMINIO: Inteligencia competitiva (A2 — catálogo estable + log de scrapes)
-- ===========================================================================
-- Identidad ESTABLE del producto competidor. Los matches apuntan aquí.
CREATE TABLE competitor_products (
  id            bigint GENERATED ALWAYS AS IDENTITY,
  competitor_id bigint NOT NULL,
  external_ref  varchar(120),   -- clave estable de la fuente, si existe
  product_name  varchar(180) NOT NULL,
  category      varchar(80),
  size_description varchar(80),
  is_active     boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_competitor_products PRIMARY KEY (id),
  CONSTRAINT uq_competitor_products UNIQUE (competitor_id, product_name, size_description),
  CONSTRAINT fk_cp_competitor FOREIGN KEY (competitor_id) REFERENCES competitors(id) ON DELETE CASCADE
);

-- Log de scrapes (alto volumen) particionado por fecha. FK al catálogo estable.
CREATE TABLE competitor_price_observations (
  id                    bigint GENERATED ALWAYS AS IDENTITY,
  competitor_product_id bigint NOT NULL,
  price                 price_amount,
  currency_code         char(3) NOT NULL DEFAULT 'COP',
  source_url            text,
  scraped_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_competitor_price_observations PRIMARY KEY (id, scraped_at),
  CONSTRAINT fk_cpo_product  FOREIGN KEY (competitor_product_id) REFERENCES competitor_products(id) ON DELETE CASCADE,
  CONSTRAINT fk_cpo_currency FOREIGN KEY (currency_code)         REFERENCES currencies(code)        ON UPDATE CASCADE ON DELETE RESTRICT
) PARTITION BY RANGE (scraped_at);
SELECT ensure_yearly_partition('competitor_price_observations', 2025);
SELECT ensure_yearly_partition('competitor_price_observations', 2026);
SELECT ensure_yearly_partition('competitor_price_observations', 2027);

CREATE TABLE product_competitor_matches (
  id                    bigint GENERATED ALWAYS AS IDENTITY,
  our_product_id        bigint NOT NULL,
  our_size_id           bigint NOT NULL,
  competitor_product_id bigint NOT NULL,   -- A2: FK estable al catálogo
  matched_by            varchar(120),
  matched_at            timestamptz NOT NULL DEFAULT now(),
  notes                 text,
  CONSTRAINT pk_product_competitor_matches PRIMARY KEY (id),
  CONSTRAINT uq_product_competitor_matches UNIQUE (our_product_id, our_size_id, competitor_product_id),
  CONSTRAINT fk_pcm_product            FOREIGN KEY (our_product_id)        REFERENCES products(id)            ON DELETE CASCADE,
  CONSTRAINT fk_pcm_size               FOREIGN KEY (our_size_id)           REFERENCES product_sizes(id)       ON DELETE CASCADE,
  CONSTRAINT fk_pcm_competitor_product FOREIGN KEY (competitor_product_id) REFERENCES competitor_products(id) ON DELETE CASCADE
);

CREATE TABLE store_products (
  id                  bigint GENERATED ALWAYS AS IDENTITY,
  store_id            bigint NOT NULL,
  product_id          bigint NOT NULL,
  is_available        boolean NOT NULL DEFAULT true,
  seasonal_start_date date,
  seasonal_end_date   date,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_store_products PRIMARY KEY (id),
  CONSTRAINT uq_store_products UNIQUE (store_id, product_id),
  CONSTRAINT ck_store_products_season CHECK (
    seasonal_end_date IS NULL OR seasonal_start_date IS NULL OR seasonal_end_date >= seasonal_start_date
  ),
  CONSTRAINT fk_sp_store   FOREIGN KEY (store_id)   REFERENCES stores(id)   ON DELETE CASCADE,
  CONSTRAINT fk_sp_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

-- ===========================================================================
-- ÍNDICES — uno por FK + compuestos por patrón de acceso
-- ===========================================================================
CREATE INDEX idx_products_category             ON products (category);
CREATE INDEX idx_product_sizes_product         ON product_sizes (product_id);
CREATE UNIQUE INDEX uq_product_sizes_default   ON product_sizes (product_id) WHERE is_default;
CREATE INDEX idx_recipe_ingredients_ingredient ON recipe_ingredients (ingredient_id);
CREATE INDEX idx_recipe_ingredients_unit       ON recipe_ingredients (recipe_unit_id);
CREATE INDEX idx_recipe_sub_recipes_sub        ON recipe_sub_recipes (sub_recipe_id);
CREATE INDEX idx_size_packaging_ingredient     ON size_packaging (packaging_ingredient_id);
CREATE INDEX idx_mie_ingredient                ON modifier_ingredient_effects (ingredient_id);
CREATE INDEX idx_iruc_unit                     ON ingredient_recipe_unit_conversions (recipe_unit_id);
CREATE INDEX idx_isub_substitute               ON ingredient_substitutes (substitute_ingredient_id);
CREATE INDEX idx_isub_unit                     ON ingredient_substitutes (recipe_unit_id);
CREATE INDEX idx_isr_regions_region            ON ingredient_substitute_regions (region_id);
CREATE INDEX idx_sr_ingredient                 ON supply_routes (ingredient_id);
CREATE INDEX idx_sr_manufacturer               ON supply_routes (manufacturer_id);
CREATE INDEX idx_sr_distributor                ON supply_routes (distributor_id);
CREATE INDEX idx_sra_route                     ON supply_route_assignments (supply_route_id);
CREATE INDEX idx_sra_region                    ON supply_route_assignments (region_id);
CREATE INDEX idx_sra_store                     ON supply_route_assignments (store_id);
CREATE INDEX idx_isrref_route                  ON ingredient_supplier_refs (supply_route_id);
CREATE INDEX idx_suc_unit                      ON supplier_unit_conversions (recipe_unit_id);
CREATE INDEX idx_srp_route_validfrom           ON supply_route_prices (supply_route_id, valid_from DESC);
CREATE INDEX idx_srp_currency                  ON supply_route_prices (currency_code);
CREATE INDEX idx_ia_ingredient                 ON ingredient_availability (ingredient_id);
CREATE INDEX idx_ia_route                      ON ingredient_availability (supply_route_id);
CREATE INDEX idx_ia_region                     ON ingredient_availability (region_id);
CREATE INDEX idx_ssh_store_ing_validfrom       ON store_supplier_history (store_id, ingredient_id, valid_from DESC);
CREATE INDEX idx_ssh_route                     ON store_supplier_history (supply_route_id);
CREATE INDEX idx_sip_ingredient                ON store_ingredient_prices (ingredient_id);
CREATE INDEX idx_pp_size                       ON product_pricing (size_id);
CREATE INDEX idx_pp_store                      ON product_pricing (store_id);
CREATE INDEX idx_pph_product_size_changed      ON product_price_history (product_id, size_id, changed_at DESC);
CREATE INDEX idx_iph_ingredient_changed        ON ingredient_price_history (ingredient_id, changed_at DESC);
CREATE INDEX idx_rcs_product_store_calc        ON recipe_cost_snapshots (product_id, store_id, calculated_at DESC);
CREATE INDEX idx_cpo_product_scraped           ON competitor_price_observations (competitor_product_id, scraped_at DESC);
CREATE INDEX idx_pcm_competitor_product        ON product_competitor_matches (competitor_product_id);
CREATE INDEX idx_pcm_size                      ON product_competitor_matches (our_size_id);
CREATE INDEX idx_sp_product                    ON store_products (product_id);

-- Búsqueda difusa por nombre (activar cuando exista el patrón de búsqueda).
CREATE INDEX idx_ingredients_name_trgm ON ingredients USING gin (name gin_trgm_ops);
CREATE INDEX idx_products_name_trgm    ON products    USING gin (name gin_trgm_ops);
CREATE INDEX idx_cp_name_trgm          ON competitor_products USING gin (product_name gin_trgm_ops);

-- ===========================================================================
-- TRIGGERS updated_at
-- ===========================================================================
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'categories','regions','recipe_units','ingredients','manufacturers','distributors',
    'competitors','modifiers','stores','products','category_margins','product_sizes',
    'recipe_ingredients','recipe_sub_recipes','size_packaging','modifier_ingredient_effects',
    'ingredient_recipe_unit_conversions','ingredient_substitutes','supply_routes',
    'ingredient_supplier_refs','product_pricing','store_products','competitor_products'
  ] LOOP
    EXECUTE format(
      'CREATE TRIGGER trg_%1$s_set_updated_at BEFORE UPDATE ON %1$s
         FOR EACH ROW EXECUTE FUNCTION set_updated_at();', t);
  END LOOP;
END;
$$;

-- C2: trigger que mantiene ingredients.current_price desde el historial.
CREATE OR REPLACE FUNCTION sync_ingredient_current_price()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE ingredients SET current_price = NEW.price, updated_at = now()
  WHERE id = NEW.ingredient_id;
  RETURN NEW;
END;
$$;
CREATE TRIGGER trg_iph_sync_current_price
  AFTER INSERT ON ingredient_price_history
  FOR EACH ROW EXECUTE FUNCTION sync_ingredient_current_price();

-- ===========================================================================
-- VISTAS / MATVIEWS
-- ===========================================================================
-- C2: precio actual = lectura directa de ingredients.current_price (vista trivial).
CREATE VIEW v_current_ingredient_price AS
SELECT id AS ingredient_id, current_price AS price
FROM ingredients
WHERE current_price IS NOT NULL;

-- C4: costo de modificador como MATERIALIZED VIEW (refrescable, no tabla obsoleta).
CREATE MATERIALIZED VIEW mv_product_modifier_cost AS
SELECT mie.modifier_id,
       SUM(mie.quantity_change * i.current_price) AS cost_impact
FROM modifier_ingredient_effects mie
JOIN ingredients i ON i.id = mie.ingredient_id
WHERE i.current_price IS NOT NULL
GROUP BY mie.modifier_id;
CREATE UNIQUE INDEX uq_mv_pmc_modifier ON mv_product_modifier_cost (modifier_id);
-- Refresco: REFRESH MATERIALIZED VIEW CONCURRENTLY mv_product_modifier_cost;
-- (tras cambios de precio o de efectos; programar con pg_cron).

CREATE VIEW v_current_ingredient_availability AS
SELECT ia.* FROM ingredient_availability ia
WHERE ia.valid_from <= CURRENT_DATE
  AND (ia.valid_until IS NULL OR ia.valid_until >= CURRENT_DATE);

CREATE VIEW v_product_effective_price AS
SELECT product_id, size_id, store_id, currency_code,
       final_price, is_manual_price, calculated_cost, effective_date
FROM product_pricing;

-- ===========================================================================
-- FUNCIONES de lógica de negocio (fuente única de verdad)
-- ===========================================================================
-- fn_resolve_supply_route (migración 0002): qué ruta usa una tienda hoy.
CREATE OR REPLACE FUNCTION fn_resolve_supply_route(
    p_ingredient_id bigint, p_store_id bigint, p_date date DEFAULT CURRENT_DATE
) RETURNS TABLE (
    assignment_id bigint, supply_route_id bigint, scope varchar, priority integer,
    manufacturer_id bigint, distributor_id bigint, is_direct boolean
) LANGUAGE sql STABLE AS $$
    SELECT * FROM (
        SELECT sra.id, sra.supply_route_id, 'store_override'::varchar, sra.priority,
               sr.manufacturer_id, sr.distributor_id, sr.is_direct
        FROM supply_route_assignments sra
        JOIN supply_routes sr ON sr.id = sra.supply_route_id
        WHERE sra.store_id = p_store_id AND sr.ingredient_id = p_ingredient_id
          AND sr.is_active AND sra.valid_from <= p_date
          AND (sra.valid_until IS NULL OR sra.valid_until > p_date)
        UNION ALL
        SELECT sra.id, sra.supply_route_id, 'region_default'::varchar, sra.priority,
               sr.manufacturer_id, sr.distributor_id, sr.is_direct
        FROM supply_route_assignments sra
        JOIN supply_routes sr ON sr.id = sra.supply_route_id
        JOIN stores s ON s.region_id = sra.region_id
        WHERE s.id = p_store_id AND sra.store_id IS NULL
          AND sr.ingredient_id = p_ingredient_id AND sr.is_active
          AND sra.valid_from <= p_date
          AND (sra.valid_until IS NULL OR sra.valid_until > p_date)
    ) candidates
    ORDER BY CASE candidates.scope WHEN 'store_override' THEN 0 ELSE 1 END, candidates.priority
    LIMIT 1;
$$;

-- A1: costo unitario de un ingrediente para una tienda — PRECEDENCIA ÚNICA.
-- 1) precio local vigente (store_ingredient_prices) > 2) precio qargo de la ruta
-- resuelta > 3) precio actual de catálogo del ingrediente. Una sola definición
-- para que ningún sistema diverja.
CREATE OR REPLACE FUNCTION fn_ingredient_unit_cost(
    p_ingredient_id bigint, p_store_id bigint, p_date date DEFAULT CURRENT_DATE
) RETURNS price_amount LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        (SELECT sip.local_price FROM store_ingredient_prices sip
          WHERE sip.store_id = p_store_id AND sip.ingredient_id = p_ingredient_id
            AND sip.valid_from <= p_date
            AND (sip.valid_until IS NULL OR sip.valid_until >= p_date)
          ORDER BY sip.valid_from DESC LIMIT 1),
        (SELECT srp.qargo_price FROM fn_resolve_supply_route(p_ingredient_id, p_store_id, p_date) r
          JOIN supply_route_prices srp ON srp.supply_route_id = r.supply_route_id
          WHERE srp.valid_from <= p_date
            AND (srp.valid_until IS NULL OR srp.valid_until >= p_date)
          ORDER BY srp.valid_from DESC LIMIT 1),
        (SELECT i.current_price FROM ingredients i WHERE i.id = p_ingredient_id)
    );
$$;

COMMIT;
