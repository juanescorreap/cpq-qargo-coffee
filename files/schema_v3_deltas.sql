-- ============================================================================
-- Qargo Coffee — Deltas v3 (sobre el esquema v2 ya implementado, head 0014)
-- PostgreSQL 15+
-- ----------------------------------------------------------------------------
-- v3 NO reescribe el esquema completo (vive en files/schema_v2_refactorizado.sql
-- + migraciones 0001-0014). Son correcciones quirúrgicas de los hallazgos del
-- análisis crítico v2 -> v3. Cada bloque = una migración Alembic candidata.
--
--   V3-1  trigger current_price: solo avanzar si el insert es el más reciente
--   V3-2  refresco automatizado de mv_product_modifier_cost (pg_cron)
--   V3-3  store_ingredient_prices: write-path close+insert (código) — ver nota
--   V3-4  scheduler de particiones + drenar DEFAULT (pg_cron)
--   V3-5  normalizar unidad de precio de ruta (price_per_unit -> FK)
--   V3-6  tabla fx_rates para coherencia/conversión multi-moneda
--   V3-7  índices parciales de estado vigente (valid_until IS NULL)
--   V3-8  prevención de ciclos en BOM (trigger recursivo)
-- ============================================================================

-- ---------------------------------------------------------------------------
-- V3-1 (CRÍTICO) — current_price no debe retroceder con inserts fuera de orden
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.sync_ingredient_current_price()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  -- Solo actualizar si NEW es la observación más reciente del ingrediente.
  -- Evita que una carga retroactiva (changed_at viejo) pise el precio actual.
  UPDATE public.ingredients i
  SET current_price = NEW.price, updated_at = now()
  WHERE i.id = NEW.ingredient_id
    AND NEW.changed_at >= COALESCE((
      SELECT max(h.changed_at)
      FROM public.ingredient_price_history h
      WHERE h.ingredient_id = NEW.ingredient_id
        AND h.changed_at <> NEW.changed_at
    ), NEW.changed_at);
  RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- V3-7 (MEDIO) — índices parciales para lectura de "fila vigente"
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_srp_route_current
  ON public.supply_route_prices (supply_route_id)
  WHERE valid_until IS NULL;

CREATE INDEX IF NOT EXISTS idx_sra_region_current
  ON public.supply_route_assignments (region_id, priority)
  WHERE valid_until IS NULL AND region_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sra_store_current
  ON public.supply_route_assignments (store_id, priority)
  WHERE valid_until IS NULL AND store_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sip_current
  ON public.store_ingredient_prices (store_id, ingredient_id)
  WHERE valid_until IS NULL;

CREATE INDEX IF NOT EXISTS idx_ssh_current
  ON public.store_supplier_history (store_id, ingredient_id)
  WHERE valid_until IS NULL;

-- ---------------------------------------------------------------------------
-- V3-6 (ALTO) — tipos de cambio para coherencia/conversión multi-moneda
-- ---------------------------------------------------------------------------
CREATE TABLE public.fx_rates (
  id          bigint GENERATED ALWAYS AS IDENTITY,
  base_code   char(3) NOT NULL,
  quote_code  char(3) NOT NULL,
  rate        numeric(18, 8) NOT NULL CHECK (rate > 0),  -- 1 base = rate quote
  valid_from  date NOT NULL DEFAULT CURRENT_DATE,
  valid_until date,
  source      varchar(120),
  created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_fx_rates PRIMARY KEY (id),
  CONSTRAINT ck_fx_rates_diff CHECK (base_code <> quote_code),
  CONSTRAINT ck_fx_rates_validity CHECK (valid_until IS NULL OR valid_until >= valid_from),
  CONSTRAINT fk_fx_base  FOREIGN KEY (base_code)  REFERENCES public.currencies(code) ON UPDATE CASCADE ON DELETE RESTRICT,
  CONSTRAINT fk_fx_quote FOREIGN KEY (quote_code) REFERENCES public.currencies(code) ON UPDATE CASCADE ON DELETE RESTRICT,
  -- Un solo tipo de cambio vigente por par y periodo.
  CONSTRAINT no_overlap_fx EXCLUDE USING gist (
    base_code WITH =, quote_code WITH =,
    daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
  )
);
CREATE INDEX idx_fx_pair_current ON public.fx_rates (base_code, quote_code) WHERE valid_until IS NULL;

-- Conversión única de verdad: monto en moneda origen -> moneda destino a una fecha.
CREATE OR REPLACE FUNCTION public.fn_convert_amount(
    p_amount numeric, p_from char(3), p_to char(3), p_date date DEFAULT CURRENT_DATE
) RETURNS numeric LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN p_from = p_to THEN p_amount
        ELSE p_amount * (
            SELECT rate FROM public.fx_rates
            WHERE base_code = p_from AND quote_code = p_to
              AND valid_from <= p_date
              AND (valid_until IS NULL OR valid_until >= p_date)
            ORDER BY valid_from DESC LIMIT 1
        )
    END;
$$;

-- ---------------------------------------------------------------------------
-- V3-5 (ALTO) — normalizar la unidad del precio de ruta
-- price_per_unit (varchar libre) -> FK a recipe_units, para que el costeo
-- pueda validar/convertir contra conversion_factor en vez de asumir.
-- (Migración con backfill manual: mapear los strings existentes a unidades.)
-- ---------------------------------------------------------------------------
ALTER TABLE public.supply_route_prices
  ADD COLUMN price_unit_id bigint REFERENCES public.recipe_units(id) ON DELETE RESTRICT;
CREATE INDEX idx_srp_price_unit ON public.supply_route_prices (price_unit_id);
-- TODO backfill: UPDATE supply_route_prices SET price_unit_id = ... según price_per_unit;
-- luego, en una migración posterior: ALTER COLUMN price_unit_id SET NOT NULL;
-- y deprecar price_per_unit.

-- ---------------------------------------------------------------------------
-- V3-8 (MEDIO) — prevención de ciclos en BOM (A->B->A)
-- Un CHECK no puede expresarlo; trigger recursivo que rechaza el ciclo.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.fn_recipe_no_cycle()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (
    WITH RECURSIVE chain(pid) AS (
      SELECT NEW.sub_recipe_id
      UNION
      SELECT rsr.sub_recipe_id
      FROM public.recipe_sub_recipes rsr
      JOIN chain ON rsr.parent_product_id = chain.pid
    )
    SELECT 1 FROM chain WHERE pid = NEW.parent_product_id
  ) THEN
    RAISE EXCEPTION 'recipe_sub_recipes: cycle detected (% -> %)',
      NEW.parent_product_id, NEW.sub_recipe_id;
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_recipe_sub_recipes_no_cycle
  BEFORE INSERT OR UPDATE ON public.recipe_sub_recipes
  FOR EACH ROW EXECUTE FUNCTION public.fn_recipe_no_cycle();

-- ---------------------------------------------------------------------------
-- V3-2 + V3-4 (CRÍTICO/ALTO) — automatización con pg_cron (Supabase lo soporta)
-- Requiere: CREATE EXTENSION IF NOT EXISTS pg_cron;  (en la base 'postgres')
-- ---------------------------------------------------------------------------
-- Refrescar la matview de costo de modificadores cada 15 min (CONCURRENTLY usa
-- el índice único uq_mv_pmc_modifier).
--   SELECT cron.schedule('refresh_mv_modifier_cost', '*/15 * * * *',
--     $$REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_product_modifier_cost$$);
--
-- Crear las particiones del año siguiente cada diciembre (con antelación).
--   SELECT cron.schedule('ensure_partitions_next_year', '0 0 1 12 *', $$
--     SELECT public.ensure_yearly_partition('public.product_price_history',    extract(year from now())::int + 1);
--     SELECT public.ensure_yearly_partition('public.ingredient_price_history', extract(year from now())::int + 1);
--     SELECT public.ensure_yearly_partition('public.recipe_cost_snapshots',    extract(year from now())::int + 1);
--     SELECT public.ensure_yearly_partition('public.competitor_price_observations', extract(year from now())::int + 1);
--   $$);
--
-- NOTA V3-4 (DEFAULT): para poder seguir creando particiones a futuro sin que el
-- DEFAULT las bloquee, drenar periódicamente las filas del DEFAULT a su partición
-- anual, o eliminar la partición DEFAULT en cuanto exista cobertura explícita
-- suficiente. Las tablas que conservan DEFAULT hoy:
--   product_price_history_default, ingredient_price_history_default,
--   recipe_cost_snapshots_default.

-- ---------------------------------------------------------------------------
-- V3-3 (CRÍTICO) — corrección en CÓDIGO, no DDL
-- ---------------------------------------------------------------------------
-- backend/routers/stores.py::upsert_ingredient_price debe seguir el patrón
-- temporal (P2): en vez de mutar la fila vigente, cerrarla
--   UPDATE store_ingredient_prices SET valid_until = CURRENT_DATE
--   WHERE store_id=? AND ingredient_id=? AND valid_until IS NULL;
-- e INSERTAR una nueva fila vigente. Así la estructura temporal (valid_from/until
-- + EXCLUDE no_overlap_sip) acumula historia real en lugar de quedar cosmética.
