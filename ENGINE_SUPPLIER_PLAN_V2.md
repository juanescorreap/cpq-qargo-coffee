# Plan definitivo V2 — Motor de Costos + Proveedores (post-evaluación)

> Reescribe `ENGINE_SUPPLIER_PLAN.md` integrando la evaluación crítica.
> Schema base v3 (head `0020`). Motor: `backend/services/cost_calculator.py`,
> `backend/services/pricing_engine.py`. Dominios reales: `price_amount numeric(14,4)`,
> `quantity_amount numeric(14,6)`, `currencies.minor_unit smallint`.
> Funciones ya existentes que reusamos: `fn_resolve_supply_route(bigint,bigint,date)`,
> `fn_ingredient_unit_cost(bigint,bigint,date)`, `fn_convert_amount(numeric,char,char,date)`.

---

## 0. Mapa de hallazgos → solución

| # | Hallazgo (evaluación) | Sección que lo cierra |
|---|---|---|
| 1 | Batch real en `pricing_engine`, no en `cost_calculator`; `_resolve_markup` N+1 | §1 Fase 1, §5.B |
| 2 | E6 quantize rompe red de tests de regresión | §1 Fase 1 vs Fase 6 |
| 3 | `fn_resolve_ingredient_sourcing` no puede elegir conversión | §2.1, §5.0022 |
| 4 | Réplica vs primary contradictorio | §3.1 |
| 5 | Eventos/retry sin transporte | §4 |
| 6 | Snapshots no idempotentes | §3.2 |
| 7 | `store_supplier_history` mezcla concerns + race EXCLUDE | §2.2 |
| 8 | Multi-moneda sin lineage FX | §3.3, §5.0021 |
| 9 | `formula_version` sin columna; snapshot sin `size_id` | §3.3, §5.0021 |
| 10 | Ingesta sin lock (race close+insert) | §4.3, §5.0024 |
| — | missing-conversion KeyError/silencio | §2.3 |

---

## 1. Plan de fases refactorizado

Regla por fase: migración(es) → código → tests verdes → commit. Orden estricto.
**Invariante de seguridad:** Fases 1–5 NO cambian ningún número de costo. La red de
tests de regresión numérica debe permanecer idéntica hasta la Fase 6.

### Fase 1 — Matar N+1 y exponencial (toda la ruta caliente, sin cambiar números)
Alcance ampliado: **incluye `pricing_engine`**, que es donde vive el loop real.
- `CostCalculator`: reescribir a `load_context()` (bulk) + `base_recipe_cost()` memoizado
  sobre el DAG (O(V+E)) + `cost_for_size()`. Reusar producto precargado (E3). Escalar por
  talla sin recomputar (E5). Guard de ciclo con `set visiting` (E8).
- `PricingEngine.calculate_all_prices`: dejar de llamar `calculate_price` por item.
  Precargar **una** vez el contexto y los **márgenes** (mata el N+1 de `_resolve_markup`).
- `get_cost_breakdown`: desglose real línea-a-línea desde el cómputo único (E4).
- **Redondeo: NO tocar.** Sigue `round(total, 2)` para no romper expected. E6 va en Fase 6.
- **Tests:** suite actual idéntica + nuevos: (a) sub-receta compartida valuada 1 vez (memo),
  (b) presupuesto de queries: contar queries del batch ≤ K (assert anti-N+1).
- **Criterio:** mismos números; query count plano respecto a #productos.

### Fase 2 — Sourcing de proveedor (B1)
- Migración `0022`: `fn_resolve_ingredient_sourcing(...recipe_unit_id...)` (§2.1).
- Código: `load_context` consume la función set-based → mapa
  `(ingredient_id, recipe_unit_id) → (unit_price, currency, purchase_qty, recipe_qty, source, provenance)`.
  `_line_cost` usa conversión **del proveedor** si `source='route'`; catálogo si `source IN ('local','catalog')`.
- **Tests:** empaque proveedor ≠ catálogo → usa conversión proveedor; sin ruta → catálogo.

### Fase 3 — Disponibilidad y sustitutos (B2)
- Migración `0023`: `fn_active_substitute` (1 nivel; §6 decisiones del plan original).
- Código: resolver disponibilidad → sustituir con `quantity_ratio`, marcar `has_substitutes`.
  Sin sustituto en shortage → política "costear original + flag `unavailable_no_substitute`".
- **Tests:** `shortage|unavailable|always`, regiones (`ingredient_substitute_regions`), sin-sustituto.

### Fase 4 — Trazabilidad + lineage (T1/T2/B3) + desacople historial (B4)
- Migración `0021` aplicada aquí en efecto: snapshot con `size_id`, `formula_version`,
  `batch_run_id`, FX lineage (§3.3). (Migrar primero; se numera 0021 por orden de dependencia.)
- Persistir `recipe_cost_snapshots` con desglose por línea (ruta/proveedor/sustituto/FX).
- `store_supplier_history` se escribe en **job aparte**, no en el batch de costos (§2.2).
- **Tests:** snapshot reconstruible; historial sin solape (EXCLUDE ya existe).

### Fase 5 — Concurrencia, cola e ingesta (T3/B5)
- Migración `0024`: tabla `calc_jobs` (outbox/queue) + triggers + `pg_cron` + `fn_ingest_route_price` con advisory lock (§4).
- Código: workers `FOR UPDATE SKIP LOCKED`, read-replica para contexto, primary para escritura,
  job marcado `done` en la **misma** transacción que los writes (idempotencia, §3.2).
- **Tests:** batch reanudable; reintento no duplica; ingesta cierra+inserta bajo concurrencia.

### Fase 6 — Redondeo por moneda (E6, AISLADO)
- Cambiar `round(total,2)` → `quantize(10**-currencies.minor_unit)`.
- **Este es el único cambio numérico.** Actualizar expected: COP `minor_unit=0` → entero.
- Se aísla para que la regresión sea revisable en un solo diff, no enterrada en Fase 1.

---

## 2. Blockers — especificación de código

### 2.1 `fn_resolve_ingredient_sourcing` con `recipe_unit_id`

La ambigüedad: `supplier_unit_conversions` está keyed por `(ingredient_ref_id, recipe_unit_id)`.
Sin la unidad de la línea no se puede elegir fila. Firma corregida + precedencia
local→route→catalog devolviendo además procedencia y conversión del proveedor:

```sql
CREATE OR REPLACE FUNCTION public.fn_resolve_ingredient_sourcing(
    p_ingredient_id  bigint,
    p_store_id       bigint,
    p_recipe_unit_id bigint,         -- NULL = la cantidad ya está en unidad de consumo
    p_date           date DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    supply_route_id  bigint,
    manufacturer_id  bigint,
    distributor_id   bigint,
    is_direct        boolean,
    unit_price       price_amount,   -- precio en la unidad de COMPRA
    price_currency   char(3),
    purchase_qty     quantity_amount, -- NULL salvo source='route'
    recipe_qty       quantity_amount, -- NULL salvo source='route'
    price_valid_from date,
    source           varchar          -- 'local' | 'route' | 'catalog'
)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    r        record;  -- ruta resuelta
    v_local  record;  -- precio local vigente
    v_srp    record;  -- precio de ruta vigente
BEGIN
    -- (1) PRECIO LOCAL de tienda (máxima precedencia, igual que fn_ingredient_unit_cost)
    SELECT sip.local_price, sip.currency_code, sip.valid_from
      INTO v_local
      FROM public.store_ingredient_prices sip
     WHERE sip.store_id = p_store_id AND sip.ingredient_id = p_ingredient_id
       AND sip.valid_from <= p_date
       AND (sip.valid_until IS NULL OR sip.valid_until >= p_date)
     ORDER BY sip.valid_from DESC LIMIT 1;

    IF FOUND THEN
        RETURN QUERY SELECT
            NULL::bigint, NULL::bigint, NULL::bigint, NULL::boolean,
            v_local.local_price, v_local.currency_code,
            NULL::quantity_amount, NULL::quantity_amount,
            v_local.valid_from, 'local'::varchar;
        RETURN;
    END IF;

    -- (2) RUTA resuelta (override tienda > regional; priority 1 > 2). 1 fila.
    SELECT * INTO r
      FROM public.fn_resolve_supply_route(p_ingredient_id, p_store_id, p_date) LIMIT 1;

    IF r.supply_route_id IS NOT NULL THEN
        SELECT srp.qargo_price, srp.currency_code, srp.valid_from
          INTO v_srp
          FROM public.supply_route_prices srp
         WHERE srp.supply_route_id = r.supply_route_id
           AND srp.valid_from <= p_date
           AND (srp.valid_until IS NULL OR srp.valid_until >= p_date)
         ORDER BY srp.valid_from DESC LIMIT 1;

        IF FOUND THEN
            -- conversión del proveedor para ESTA unidad de receta (desambiguada)
            RETURN QUERY
            SELECT r.supply_route_id, r.manufacturer_id, r.distributor_id, r.is_direct,
                   v_srp.qargo_price, v_srp.currency_code,
                   suc.purchase_qty, suc.recipe_qty,
                   v_srp.valid_from, 'route'::varchar
              FROM public.ingredient_supplier_refs isr
              LEFT JOIN public.supplier_unit_conversions suc
                     ON suc.ingredient_ref_id = isr.id
                    AND suc.recipe_unit_id IS NOT DISTINCT FROM p_recipe_unit_id
             WHERE isr.ingredient_id = p_ingredient_id
               AND isr.supply_route_id = r.supply_route_id
               AND isr.is_active = true
             LIMIT 1;
            -- Nota: si purchase_qty/recipe_qty vienen NULL (no hay conversión proveedor
            -- para esa unidad) el motor cae a ingredient.conversion_factor (catálogo).
            RETURN;
        END IF;
    END IF;

    -- (3) CATÁLOGO (fallback). Moneda base del sistema = COP.
    RETURN QUERY
    SELECT NULL::bigint, NULL::bigint, NULL::bigint, NULL::boolean,
           COALESCE(i.current_price, i.purchase_price, 0)::price_amount, 'COP'::char(3),
           NULL::quantity_amount, NULL::quantity_amount,
           NULL::date, 'catalog'::varchar
      FROM public.ingredients i WHERE i.id = p_ingredient_id;
END;
$$;
```

**Uso en el motor (set-based, precarga única):** el `load_context` la llama vía
`LATERAL` sobre el conjunto distinto de `(ingredient_id, recipe_unit_id)` que aparecen
en las recetas a costear — una sola query, no por fila.

### 2.2 Desacoplar `store_supplier_history` (B4) del batch

**Problema:** escribir close+insert dentro del batch paralelo provoca contención y
violación del `EXCLUDE` (varios workers tocan el mismo `store_id+ingredient_id`).
Además mezcla "auditoría de sourcing" con "cálculo de costo".

**Solución — job de resolución de sourcing separado, secuencial e idempotente:**
- El batch de costos **no escribe** `store_supplier_history`. Solo lee sourcing.
- Un job dedicado `sourcing_sync` (un solo worker, o serializado por advisory lock)
  recorre `(store, ingredient)` activos, compara la ruta resuelta hoy contra la fila
  vigente (`valid_until IS NULL`) y aplica close+insert **solo si cambió**.
- Se dispara por evento (cambio de assignment/ruta), no por cada cálculo de precio.

```python
def sync_store_supplier_history(db, store_id: int, as_of: date) -> int:
    """Reconciliación idempotente. Serializada por ingrediente vía advisory lock.
    NO corre dentro del batch de costos. Disparada por on_route_change."""
    changed = 0
    for ing_id, new_route in resolve_active_routes(db, store_id, as_of):
        # lock por (store,ingredient) → cero contención con el resto
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                   {"k": f"ssh:{store_id}:{ing_id}"})
        cur = db.execute(text("""
            SELECT supply_route_id FROM store_supplier_history
            WHERE store_id=:s AND ingredient_id=:i AND valid_until IS NULL
            FOR UPDATE"""), {"s": store_id, "i": ing_id}).scalar()
        if cur == new_route:
            continue
        if cur is not None:
            db.execute(text("""UPDATE store_supplier_history SET valid_until=:d
                WHERE store_id=:s AND ingredient_id=:i AND valid_until IS NULL"""),
                {"d": as_of, "s": store_id, "i": ing_id})
        db.execute(text("""INSERT INTO store_supplier_history
            (store_id, ingredient_id, supply_route_id, valid_from)
            VALUES (:s,:i,:r,:d)"""),
            {"s": store_id, "i": ing_id, "r": new_route, "d": as_of})
        changed += 1
    db.commit()
    return changed
```

### 2.3 Política de conversión faltante (no KeyError, no silencio)

Tres casos distintos, tres comportamientos definidos:

| Caso | Hoy | Política V2 |
|---|---|---|
| Falta `recipe_unit` → `usage_unit` (catálogo) | `ValueError` (línea 445) | **mantener error** en cálculo on-demand; en **batch**: costear con factor=1, marcar línea `missing_recipe_unit_conversion=true`, no abortar el chunk |
| Falta conversión **proveedor** para la unidad | (no existe) | caer a `ingredient.conversion_factor`, marcar `fell_back_to_catalog_conversion=true` |
| `conversion_factor` NULL/0 | `return 0` silencioso (línea 560) | costo 0 pero marcar línea `no_conversion_factor=true` |

Los flags viven en `snapshot_detail` por línea → la inconsistencia es auditable, el
batch no se cae, y un reporte puede listar exactamente qué corregir.

```python
def _line_cost(ctx, line, scale) -> tuple[Decimal, dict]:
    flags = {}
    src = ctx.sourcing[(line.ingredient_id, line.recipe_unit_id)]
    qty = line.quantity
    if line.recipe_unit_id is not None:
        conv = ctx.unit_conv.get((line.ingredient_id, line.recipe_unit_id))
        if conv is None:
            if ctx.mode == "batch":
                conv = Decimal(1); flags["missing_recipe_unit_conversion"] = True
            else:
                raise MissingConversionError(line.ingredient_id, line.recipe_unit_id)
        qty *= conv
    if line.scales_with_size:
        qty *= scale
    if src.recipe_qty and src.purchase_qty:          # conversión del proveedor
        denom = src.recipe_qty / src.purchase_qty
    else:                                            # fallback catálogo
        denom = ctx.ingredients[line.ingredient_id].conversion_factor
        if src.source == "route":
            flags["fell_back_to_catalog_conversion"] = True
    if not denom:
        flags["no_conversion_factor"] = True
        return Decimal(0), {**_line_meta(line, src), **flags}
    # yields...
    ing = ctx.ingredients[line.ingredient_id]
    if ing.yield_percentage and ing.yield_percentage > 0: qty /= ing.yield_percentage
    if 0 < line.process_yield_loss < 100:             qty /= (line.process_yield_loss/100)
    unit_cost = src.unit_price / denom
    return unit_cost * qty, {**_line_meta(line, src), **flags}
```

---

## 3. Concurrencia, persistencia, idempotencia, lineage

### 3.1 Lectura de réplica / escritura a primary (flujo exacto)

```
                  ┌──────────────── READ REPLICA (solo SELECT) ────────────────┐
worker claim job  │  load_context(replica, store_id, product_ids)              │
   (primary) ───► │   - recipe_ingredients, sub_recipes, sizes, packaging      │
                  │   - ingredients, unit_conv                                 │
                  │   - fn_resolve_ingredient_sourcing (LATERAL set-based)     │
                  │   - fn_active_substitute (LATERAL set-based)               │
                  └────────────────────────────────────────────────────────────┘
                                   │  CalcContext (frozen, sin sesión)
                                   ▼  compute puro (CPU, sin I/O)
                  ┌──────────────── PRIMARY (1 transacción/chunk) ─────────────┐
                  │  upsert product_pricing                                    │
                  │  insert product_price_history (solo cambios)              │
                  │  insert recipe_cost_snapshots (append-only)               │
                  │  UPDATE calc_jobs SET status='done'  ← misma tx           │
                  │  COMMIT                                                    │
                  └────────────────────────────────────────────────────────────┘
```

Reglas:
- **Réplica = solo el contexto inmutable.** Cero escrituras. Tolerante a lag de réplica
  porque el snapshot registra `price_valid_from` por línea → el cálculo es reproducible
  aunque la réplica esté unos segundos atrás.
- **Primary = todas las escrituras**, agrupadas en una transacción por chunk.
- Claim de job va a **primary** (es escritura: `FOR UPDATE SKIP LOCKED`).
- Cálculo on-demand (1 producto, endpoint HTTP) usa primary directo, sin réplica.

### 3.2 Idempotencia: el job, no el snapshot

`recipe_cost_snapshots` es **append-only por diseño** (auditoría). No se deduplica el
snapshot; se evita re-ejecutar el chunk. Mecanismo:

- **Una transacción por chunk** que escribe pricing + history + snapshots **y** marca
  `calc_jobs.status='done'`. Atómico.
  - Crash **antes** del commit → rollback total → re-claim → reintento limpio (0 filas huérfanas).
  - Crash **después** del commit → job ya `done` → nunca re-claimado → 0 duplicados.
- `product_pricing` es upsert por clave → idempotente aun si se reprocesa.
- `batch_run_id uuid` agrupa todos los snapshots de una corrida → observabilidad,
  retención ("borrar snapshots de runs > N días") y comparación run-vs-run. **No** es
  clave de dedup; la dedup la garantiza el ciclo de vida del job.

```python
def process_chunk(primary, replica, job):
    ctx = load_context(replica, job.store_id, job.product_ids)   # RÉPLICA
    results = compute_all(ctx, job.product_ids)                  # CPU puro
    try:
        persist_pricing(primary, results)                       # upsert
        persist_history(primary, results)                       # solo cambios
        persist_snapshots(primary, results, batch_run_id=job.run_id)
        primary.execute(text(
            "UPDATE calc_jobs SET status='done', finished_at=now() WHERE id=:id"),
            {"id": job.id})
        primary.commit()                                        # ← atómico
    except Exception:
        primary.rollback()
        requeue(primary, job)                                   # attempts++ o dead-letter
```

### 3.3 Lineage financiero en el snapshot

Falta hoy: `size_id`, `formula_version`, `batch_run_id`, y la tasa FX usada.
Columnas top-level (consultables) + detalle por línea en JSONB.

`snapshot_detail` por ingrediente:
```json
{ "ingredient_id":1, "recipe_unit_id":7, "supply_route_id":5,
  "manufacturer_id":3, "distributor_id":null, "source":"route",
  "is_substitute":false, "original_ingredient_id":null, "quantity_ratio":1.0,
  "qty":252.63, "unit_price":4500, "price_currency":"USD",
  "fx_rate":4100.50, "fx_rate_date":"2026-06-01", "amount_in_store_ccy":1136840,
  "price_valid_from":"2026-01-01", "line_cost":1136.84,
  "flags":{"fell_back_to_catalog_conversion":false} }
```
Top-level nuevos: `size_id`, `formula_version`, `batch_run_id`, y para el agregado
`fx_rate`/`fx_rate_date` (la conversión dominante; el detalle por línea siempre manda).
Suma multi-moneda: cada línea se normaliza con `fn_convert_amount(amount, line_ccy,
store_ccy, price_valid_from)` **antes** de sumar; el rate y su fecha se persisten.

---

## 4. Transporte de eventos e ingesta (PostgreSQL nativo)

### 4.1 Cola basada en tabla (Outbox + Jobs) — `calc_jobs`

Una sola tabla cubre: cola de trabajo, outbox transaccional, checkpoint y dead-letter.

```sql
CREATE TYPE calc_job_status AS ENUM ('pending','running','done','failed','dead');

CREATE TABLE public.calc_jobs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id       uuid        NOT NULL DEFAULT gen_random_uuid(),
    job_type     varchar(40) NOT NULL,        -- 'batch_chunk'|'price_change'|'route_change'|'sourcing_sync'
    store_id     bigint,
    product_ids  bigint[]    NOT NULL DEFAULT '{}',
    payload      jsonb       NOT NULL DEFAULT '{}',
    status       calc_job_status NOT NULL DEFAULT 'pending',
    priority     smallint    NOT NULL DEFAULT 100,  -- menor = antes
    attempts     smallint    NOT NULL DEFAULT 0,
    max_attempts smallint    NOT NULL DEFAULT 5,
    locked_at    timestamptz,
    locked_by    text,
    not_before   timestamptz NOT NULL DEFAULT now(), -- backoff
    last_error   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz
);
CREATE INDEX idx_calc_jobs_claim ON public.calc_jobs (priority, not_before)
    WHERE status = 'pending';
```

**Claim sin contención** (N workers, cero locks largos):
```sql
WITH j AS (
  SELECT id FROM public.calc_jobs
   WHERE status='pending' AND not_before <= now()
   ORDER BY priority, not_before
   FOR UPDATE SKIP LOCKED
   LIMIT 1)
UPDATE public.calc_jobs c
   SET status='running', locked_at=now(), locked_by=:worker, attempts=attempts+1
  FROM j WHERE c.id=j.id
RETURNING c.*;
```

**Outbox transaccional `on_price_change`** — el evento se enola en la **misma tx** que
escribe el precio, así que es imposible "precio cambiado pero recálculo perdido":
```sql
CREATE OR REPLACE FUNCTION public.fn_enqueue_price_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO public.calc_jobs (job_type, payload)
    VALUES ('price_change',
            jsonb_build_object('ingredient_id', NEW.ingredient_id,
                               'changed_at', now()));
    RETURN NEW;
END; $$;

CREATE TRIGGER trg_outbox_ingredient_price
    AFTER INSERT ON public.ingredient_price_history
    FOR EACH ROW EXECUTE FUNCTION public.fn_enqueue_price_change();
-- (igual para supply_route_prices y supply_route_assignments → 'route_change')
```
El worker que procesa `price_change` expande con `reverse_bom_closure(ingredient_id)`
(CTE recursiva sobre `recipe_ingredients ∪ recipe_sub_recipes`) y crea jobs `batch_chunk`
para los productos afectados → **recálculo incremental** real.

### 4.2 `pg_cron` (ya existe, migración 0020): tres responsabilidades

```sql
-- (a) Recompute completo nocturno → siembra chunks
SELECT cron.schedule('nightly_full_recompute', '0 3 * * *', $$
  INSERT INTO public.calc_jobs (job_type, store_id, product_ids, priority)
  SELECT 'batch_chunk', s.id, arr, 50
  FROM public.stores s
  CROSS JOIN LATERAL (
    SELECT array_agg(p.id) AS arr FROM public.products p WHERE p.is_active
  ) c WHERE arr IS NOT NULL;
$$);

-- (b) Reaper: jobs 'running' colgados > 15 min vuelven a 'pending' (o dead-letter)
SELECT cron.schedule('calc_jobs_reaper', '*/5 * * * *', $$
  UPDATE public.calc_jobs
     SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
         not_before = now() + (interval '1 min' * power(2, attempts)),  -- backoff exp
         locked_at = NULL, locked_by = NULL
   WHERE status='running' AND locked_at < now() - interval '15 min';
$$);

-- (c) Retención de snapshots por run (append-only crece): conserva 90 días
SELECT cron.schedule('snapshot_retention', '30 3 * * *', $$
  DELETE FROM public.recipe_cost_snapshots
   WHERE calculated_at < now() - interval '90 days';
$$);
```
`enqueue_retry` = el reaper + el `requeue` del worker (attempts++, backoff exponencial,
a `dead` cuando supera `max_attempts`). Sin broker externo: Postgres es el bus.

> ¿Workers Python o pg_cron ejecuta el cálculo? El **cálculo pesado corre en Python**
> (proceso, `ProcessPoolExecutor`) haciendo poll del claim. `pg_cron` solo **siembra y
> mantiene** la cola (tareas SQL ligeras). No se mete CPU de Decimal en el scheduler.

### 4.3 Ingesta concurrente B5 — advisory lock por ruta

Race: dos ingestas para la misma `supply_route_id` hacen close+insert y dejan dos filas
vigentes (o chocan con `no_overlap_srp`). Serializar **por ruta** con advisory xact lock
(grano fino, sin penalizar otras rutas; preferible a `SERIALIZABLE` global que forzaría
retries por serialization_failure):

```sql
CREATE OR REPLACE FUNCTION public.fn_ingest_route_price(
    p_route_id bigint, p_list price_amount, p_qargo price_amount,
    p_ccy char(3), p_per_unit varchar, p_source varchar, p_by varchar,
    p_valid_from date DEFAULT CURRENT_DATE)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE v_id bigint;
BEGIN
    -- serializa SOLO esta ruta durante la transacción
    PERFORM pg_advisory_xact_lock(hashtextextended('srp:'||p_route_id, 0));

    IF p_qargo > p_list THEN
        RAISE EXCEPTION 'qargo_price (%) > list_price (%)', p_qargo, p_list;
    END IF;

    UPDATE public.supply_route_prices
       SET valid_until = p_valid_from - 1
     WHERE supply_route_id = p_route_id AND valid_until IS NULL;

    INSERT INTO public.supply_route_prices
        (supply_route_id, list_price, qargo_price, currency_code,
         price_per_unit, valid_from, source, created_by)
    VALUES (p_route_id, p_list, p_qargo, p_ccy, p_per_unit, p_valid_from, p_source, p_by)
    RETURNING id INTO v_id;

    -- outbox: dispara recálculo de los productos que usan el ingrediente de la ruta
    INSERT INTO public.calc_jobs (job_type, payload)
    SELECT 'route_change', jsonb_build_object('supply_route_id', p_route_id,
                                              'ingredient_id', sr.ingredient_id)
      FROM public.supply_routes sr WHERE sr.id = p_route_id;
    RETURN v_id;
END; $$;
```
La ingesta batch/CSV llama esta función fila por fila dentro de su transacción; el lock
garantiza que dos cargas concurrentes de la misma ruta se serialicen y el `EXCLUDE`
`no_overlap_srp` queda como segunda red.

---

## 5. Entregables — DDL y código

### Migración `0021_snapshot_lineage`
```python
revision = "0021_snapshot_lineage"; down_revision = "0020_pg_cron_jobs"

UPGRADE = r"""
ALTER TABLE public.recipe_cost_snapshots
  ADD COLUMN size_id         bigint,
  ADD COLUMN formula_version varchar(40) NOT NULL DEFAULT 'v1',
  ADD COLUMN batch_run_id    uuid,
  ADD COLUMN fx_rate         numeric(18,8),
  ADD COLUMN fx_rate_date    date;
-- size_id nullable (snapshots viejos no lo tienen); FK valida los nuevos.
ALTER TABLE public.recipe_cost_snapshots
  ADD CONSTRAINT fk_rcs_size FOREIGN KEY (size_id)
      REFERENCES public.product_sizes(id) ON DELETE RESTRICT;
CREATE INDEX idx_rcs_run  ON public.recipe_cost_snapshots (batch_run_id);
CREATE INDEX idx_rcs_psz  ON public.recipe_cost_snapshots (product_id, store_id, size_id, calculated_at DESC);
"""
DOWNGRADE = r"""
DROP INDEX IF EXISTS idx_rcs_psz; DROP INDEX IF EXISTS idx_rcs_run;
ALTER TABLE public.recipe_cost_snapshots
  DROP CONSTRAINT IF EXISTS fk_rcs_size,
  DROP COLUMN IF EXISTS fx_rate_date, DROP COLUMN IF EXISTS fx_rate,
  DROP COLUMN IF EXISTS batch_run_id, DROP COLUMN IF EXISTS formula_version,
  DROP COLUMN IF EXISTS size_id;
"""
```
> ADD COLUMN sobre tabla particionada (`PARTITION BY RANGE`) propaga a todas las
> particiones automáticamente en PG ≥ 11. OK.

### Migración `0022_fn_sourcing` — `fn_resolve_ingredient_sourcing` (§2.1, body completo arriba).
### Migración `0023_fn_active_substitute`
```sql
CREATE OR REPLACE FUNCTION public.fn_active_substitute(
    p_ingredient_id bigint, p_store_id bigint, p_date date DEFAULT CURRENT_DATE)
RETURNS TABLE (substitute_ingredient_id bigint, quantity_ratio numeric,
               cost_impact_pct numeric) LANGUAGE sql STABLE AS $$
  WITH avail AS (   -- ¿el original está no-disponible por ruta o región hoy?
    SELECT 1 FROM public.ingredient_availability ia
     LEFT JOIN public.stores s ON s.id = p_store_id
     WHERE ia.ingredient_id = p_ingredient_id
       AND ia.status IN ('shortage','discontinued','seasonal')
       AND ia.valid_from <= p_date
       AND (ia.valid_until IS NULL OR ia.valid_until >= p_date)
       AND (ia.region_id IS NULL OR ia.region_id = s.region_id)
     LIMIT 1)
  SELECT isub.substitute_ingredient_id, isub.quantity_ratio, isub.cost_impact_pct
    FROM public.ingredient_substitutes isub
   WHERE isub.original_ingredient_id = p_ingredient_id
     AND isub.valid_from <= p_date
     AND (isub.valid_until IS NULL OR isub.valid_until >= p_date)
     AND (isub.activation_condition = 'always' OR EXISTS (SELECT 1 FROM avail))
     AND (NOT EXISTS (SELECT 1 FROM public.ingredient_substitute_regions r
                       WHERE r.substitute_id = isub.id)               -- global
          OR EXISTS (SELECT 1 FROM public.ingredient_substitute_regions r
                      JOIN public.stores s ON s.id = p_store_id
                     WHERE r.substitute_id = isub.id AND r.region_id = s.region_id))
   ORDER BY isub.valid_from DESC LIMIT 1;   -- 1 nivel, el más reciente
$$;
```
### Migración `0024_calc_queue` — `calc_job_status`, `calc_jobs`, triggers outbox,
`fn_ingest_route_price`, y los `cron.schedule(...)` de §4.2 (DDL completos arriba).

### Código — `load_context` (precarga, frozen) + `PricingEngine` loop

```python
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class Sourcing:
    unit_price: Decimal; currency: str
    purchase_qty: Decimal | None; recipe_qty: Decimal | None
    source: str; supply_route_id: int | None
    manufacturer_id: int | None; distributor_id: int | None
    price_valid_from: object | None

@dataclass(frozen=True)
class CalcContext:
    recipe_lines: dict[int, list]          # product_id -> [RecipeLine]
    sub_recipes:  dict[int, list]          # parent_id  -> [SubRef]
    packaging:    dict[int, list]          # size_id    -> [PkgLine]
    sizes:        dict[int, list]          # product_id -> [SizeInfo]
    ingredients:  dict[int, object]        # id -> IngredientInfo
    unit_conv:    dict[tuple[int,int], Decimal]
    sourcing:     dict[tuple[int,int], Sourcing]   # (ingredient_id, recipe_unit_id)
    substitutes:  dict[int, object]        # ingredient_id -> (sub_id, ratio, impact)
    markups:      dict[tuple, Decimal]     # (product_id,size_id,store_id) -> markup
    currency_minor: dict[str, int]
    formula_version: str
    mode: str                               # 'batch' | 'ondemand'

def load_context(db, store_id, product_ids, mode="batch") -> CalcContext:
    # Una query por tabla (todas filtradas por product_ids). Sin loops de I/O.
    lines = bulk_recipe_lines(db, product_ids)         # 1 query
    subs  = bulk_sub_recipes(db, product_ids)          # 1 query (+ cierre BOM)
    all_pids = product_ids | bom_closure_ids(subs)
    sizes = bulk_sizes(db, all_pids)                   # 1 query
    pkg   = bulk_packaging(db, size_ids(sizes))        # 1 query
    ings  = bulk_ingredients(db, ingredient_ids(lines))# 1 query
    uconv = bulk_unit_conv(db, ...)                    # 1 query
    # SOURCING set-based: 1 query LATERAL sobre (ingredient_id, recipe_unit_id) distintos
    sourcing = db.execute(text("""
        SELECT k.ingredient_id, k.recipe_unit_id, s.*
          FROM (SELECT DISTINCT ingredient_id, recipe_unit_id
                  FROM recipe_ingredients WHERE product_id = ANY(:pids)) k
          CROSS JOIN LATERAL
            fn_resolve_ingredient_sourcing(k.ingredient_id, :store, k.recipe_unit_id, :d) s
    """), {"pids": list(all_pids), "store": store_id, "d": as_of}).all()
    subs_map = bulk_active_substitutes(db, store_id, ingredient_ids(lines), as_of)  # 1 q
    markups  = bulk_markups(db, product_ids, store_id)   # 1 query → mata _resolve_markup N+1
    return CalcContext(... frozen ...)
```

```python
class PricingEngine:
    def calculate_all_prices(self, store_id=None, save_to_db=False, run_id=None):
        product_ids = active_product_ids(self.db)              # 1 query
        ctx = load_context(self.db, store_id, set(product_ids))  # precarga única
        calc = _PureCalculator(ctx)                           # motor puro, sin sesión
        memo: dict[int, BaseCost] = {}
        results = []
        for pid in product_ids:
            base = calc.base_recipe_cost(pid, memo, set())     # memo compartido
            for sz in ctx.sizes.get(pid, []):
                cost = calc.cost_for_size(base, sz.scale_factor, ctx, store_id)  # CPU
                mk = ctx.markups.get((pid, sz.id, store_id), _DEFAULT_MARKUP)    # O(1)
                results.append(build_result(pid, sz, cost, mk, store_id))
        if save_to_db:
            persist_all(self.db, results, run_id)              # 1 tx
        return summarize(results)
```

### Compatibilidad on-demand (decisión: HÍBRIDO)

El motor puro recibe `CalcContext`, pero los 11 callsites de producción
(`routers/costs.py`, `costs_ui.py`, `report_generator.py`) y los tests usan hoy
`CostCalculator(db).calculate_product_cost(pid, size, store)`. Decisión tomada:

- **Fase 1 — wrapper de compatibilidad.** Se conserva la API `CostCalculator(db)` +
  `.calculate_product_cost(...)` / `.get_cost_breakdown(...)`. Internamente arma un
  contexto de 1 producto y delega al motor puro. **0 callers cambian, tests verdes sin
  tocar** → la red de regresión numérica es genuina (invariante §1 intacto).
- **Routers on-demand (1 producto) quedan con wrapper permanente.** No hay memo cruzado
  que aprovechar con 1 producto → el wrapper es la interfaz correcta, no deuda.
- **Fase posterior — migrar SOLO `report_generator`** a la API de contexto: su loop por
  producto (líneas 136-139, 452-459) sí gana con contexto compartido + memo.

```python
class CostCalculator:
    """Wrapper de compat (on-demand). El motor puro vive en _PureCalculator(ctx)."""
    def __init__(self, db_session):
        self.db = db_session

    def calculate_product_cost(self, product_id, size_id=None, store_id=None):
        ctx = load_context(self.db, store_id, {product_id}, mode="ondemand")
        pure = _PureCalculator(ctx)
        base = pure.base_recipe_cost(product_id, memo={}, visiting=set())
        scale = _scale_for(ctx, product_id, size_id)     # default size si None
        return pure.cost_for_size(base, scale, ctx, store_id)

    def get_cost_breakdown(self, product_id, size_id=None, store_id=None):
        ctx = load_context(self.db, store_id, {product_id}, mode="ondemand")
        pure = _PureCalculator(ctx)
        base = pure.base_recipe_cost(product_id, memo={}, visiting=set())
        return _build_breakdown(base, ctx, product_id, size_id, store_id)  # cierra E4
```
> `_PureCalculator` = la clase de contexto inmutable de abajo. `CostCalculator(db)` es
> solo la fachada que la alimenta. `PricingEngine` (batch) usa `_PureCalculator(ctx)`
> directo con un contexto multi-producto y memo compartido.

### Código — solucionador del DAG (`_PureCalculator` con contexto inmutable)

```python
@dataclass
class BaseCost:
    fixed: Decimal       # líneas/sub-recetas/labor que NO escalan (a scale=1)
    scalable: Decimal    # las que SÍ escalan (a scale=1)
    detail: list         # lineage por línea (para snapshot_detail)

class _PureCalculator:
    """Puro: recibe CalcContext, cero sesión DB. Reusable y paralelizable.
    Fachada de compat = CostCalculator(db) (arriba)."""
    def __init__(self, ctx: CalcContext): self.ctx = ctx

    def base_recipe_cost(self, pid, memo, visiting) -> BaseCost:
        if pid in memo: return memo[pid]            # cada nodo del DAG: 1 vez (O(V+E))
        if pid in visiting: raise CycleError(pid)   # defensa O(1); BD ya lo impide (V3-8)
        visiting.add(pid)
        fixed = Decimal(0); scal = Decimal(0); detail = []

        for line in self.ctx.recipe_lines.get(pid, []):
            line_cost, meta = self._resolve_line(line)   # incl. sustituto + flags
            detail.append(meta)
            (scal := scal + line_cost) if line.scales_with_size else (fixed := fixed + line_cost)

        for sub in self.ctx.sub_recipes.get(pid, []):
            sb = self.base_recipe_cost(sub.sub_id, memo, visiting)   # post-orden memo
            unit = sb.fixed + sb.scalable                            # base talla=1
            if sub.scales_with_size: scal += unit * sub.qty
            else:                    fixed += unit * sub.qty

        fixed += self.ctx.labor.get(pid, Decimal(0))     # labor no escala (reusa producto)
        visiting.discard(pid)
        memo[pid] = BaseCost(fixed, scal, detail)
        return memo[pid]

    def cost_for_size(self, base: BaseCost, scale, ctx, store_id) -> Decimal:
        raw = base.fixed + base.scalable * scale
        # Fase 1: redondeo legacy (NO cambia números). Fase 6 reemplaza por:
        #   minor = ctx.currency_minor[store_ccy]; return raw.quantize(Decimal(10)**-minor)
        return round(raw, 2)

    def _resolve_line(self, line) -> tuple[Decimal, dict]:
        sub = self.ctx.substitutes.get(line.ingredient_id)   # 1 nivel
        if sub:
            line = line.with_substitute(sub.sub_id, sub.ratio)
        return _line_cost(self.ctx, line, scale=Decimal(1))  # §2.3
```

---

## 6. Resumen de garantías

| Riesgo evaluación | Estado V2 |
|---|---|
| Exponencial sub-recetas | memo DAG O(V+E), 1 visita/nodo |
| N+1 precio | `fn_resolve_ingredient_sourcing` set-based, 1 query |
| N+1 markup | `bulk_markups` precargado, lookup O(1) |
| E6 rompe tests | aislado en Fase 6, único diff numérico |
| Ambigüedad conversión | `recipe_unit_id` en la firma |
| Race historial proveedor | job aparte + advisory lock por (store,ingredient) |
| Conversión faltante | política explícita + flags en snapshot |
| Réplica/primary | contexto←réplica, escrituras→primary, 1 tx/chunk |
| Snapshot duplicado | job marcado `done` en la misma tx; upsert pricing |
| Lineage FX | `fn_convert_amount` + rate/fecha persistidos por línea |
| Eventos handwave | outbox `calc_jobs` + `FOR UPDATE SKIP LOCKED` + pg_cron reaper |
| Ingesta race | `fn_ingest_route_price` con `pg_advisory_xact_lock` + EXCLUDE |
| Reanudable | claim/backoff/dead-letter en `calc_jobs` |
```
