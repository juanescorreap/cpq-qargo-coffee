# Plan de implementación — Esquema v2 (Qargo Coffee)

> **Documento de contexto completo para Claude Code.**
> Generado 2026-06-04 a partir del análisis DBA del esquema refactorizado v1.
> DDL objetivo: `files/schema_v2_refactorizado.sql`.
> Lee este documento entero antes de ejecutar cualquier migración.

---

## 0. TL;DR

El esquema **v1** (migraciones `0001`-`0004`) ya está implementado y es sólido, pero arrastra deudas reales: sustitutos sin historial, vista de precio que no escala, dato derivado obsoleto, doble fuente de precio, e identidad volátil de competidores. **v2** las cierra. Este plan describe cómo llevar v1 → v2 sin romper la app (SQLAlchemy + FastAPI + Alembic + Jinja2/HTMX).

**v2 NO está aplicado.** `files/schema_v2_refactorizado.sql` es la propuesta validada conceptualmente, no migrada.

---

## 1. Estado actual (punto de partida)

### 1.1 Rama y BD
- Rama git: `refactor/greenfield-schema` (NO commiteada).
- Supabase reseteada (greenfield, datos de prueba). Revisión Alembic actual: **`0003_restore_p7_invariants`**.
- Migración `0004_category_slug_underscore` editada pero **pendiente de aplicar** (relaja el CHECK de slug de `categories` para admitir `_` y `-`; el id se acortó porque `alembic_version.version_num` es `varchar(32)`).

### 1.2 Migraciones existentes (`backend/migrations/versions/`)
| Rev | Qué hace |
|-----|----------|
| `0001_initial_schema` | DDL completo del target validado (`files/schema_refactorizado.sql`), 37 tablas + 12 particiones, vía `op.execute` |
| `0002_resolve_supply_route_fn` | Restaura `fn_resolve_supply_route` (omitida en el DDL validado) |
| `0003_restore_p7_invariants` | Restaura 2× EXCLUDE en `supply_route_assignments`, 1× EXCLUDE en `store_supplier_history`, CHECK `is_direct↔distributor` |
| `0004_category_slug_underscore` | Relaja CHECK slug categories (PENDIENTE de aplicar) |
| `_archived/` | Migraciones viejas pre-refactor (no usar) |

### 1.3 Estado del código
- **Modelos** (`backend/models/`): ya migrados a v1 (bigint Identity, domains numéricos, FKs con ON DELETE/UPDATE, particionado, FK compuesta en `ProductCompetitorMatch`, `affects_regions` eliminado). Nuevos: `currency.py`, `ingredient_substitute_region.py`.
- **Schemas** (`backend/schemas/`): `currency.py` nuevo; `ingredient.canonical_unit`; validador `default_currency_code` en store.
- **Routers**: `currencies.py` nuevo, registrado en `main.py`.
- **Servicios**: `pricing_engine.save_pricing` arreglado (upsert por `(product,size,store,currency)`, no por `effective_date`).
- **Tests**: `conftest.py` con fixture autouse `seed_categories`. Suite aún en rojo (faltaba aplicar `0004` + posibles ajustes).

### 1.4 Deuda pendiente del refactor v1 (cerrar antes o junto con v2)
1. Aplicar `0004` (`alembic upgrade head`).
2. Correr suite completa y arreglar fallos restantes (ver §6).
3. Decidir si v2 reemplaza el cierre de v1 o se hace después.

---

## 2. Hallazgos del análisis (qué corrige v2)

Cada uno tiene impacto real, no cosmético.

### 🔴 Críticos
- **C1 — `ingredient_substitutes UNIQUE(orig,sub)` rompe el patrón temporal P2.**
  La tabla tiene `valid_from`/`valid_until` pero el UNIQUE permite una sola fila por par → no puedes cerrar e insertar (re-aprobar) el mismo par. **Fix v2:** `EXCLUDE USING gist (orig =, sub =, daterange &&)`.
- **C2 — `v_current_ingredient_price` no escala.**
  `DISTINCT ON` sobre historial particionado = full scan por lectura; `v_product_modifier_cost` la amplifica. **Fix v2:** columna denormalizada `ingredients.current_price` + trigger `sync_ingredient_current_price` en INSERT del historial; la vista pasa a ser lectura O(1).
- **C3 — `ingredient_availability` sin CHECK de ámbito + FKs SET NULL → huérfanos.**
  Borrar ruta/región deja filas con ambos NULL. **Fix v2:** re-añadir `ck_ia_scope` + FKs `ON DELETE CASCADE`.
- **C4 — `product_modifier_costs` = derivado persistido sin invalidación.**
  Se vuelve obsoleto al cambiar precios. **Fix v2:** eliminar tabla → `mv_product_modifier_cost` (MATERIALIZED VIEW con índice único + REFRESH CONCURRENTLY).

### 🟠 Altos
- **A1 — Doble fuente de precio de ingrediente.**
  `store_ingredient_prices` (sin temporalidad) vs `supply_route_prices` (temporal), sin precedencia definida → cálculos divergentes. **Fix v2:** dar temporalidad a `store_ingredient_prices` (`valid_from/until` + EXCLUDE) y crear `fn_ingredient_unit_cost(ingredient, store, date)` con precedencia única: **local vigente → qargo_price de ruta resuelta → current_price de catálogo**.
- **A2 — `competitor_products` mezcla identidad y evento de scrape.**
  Los matches apuntan a un scrape puntual; re-scrapear crea ids nuevos → matches obsoletos. **Fix v2:** split en `competitor_products` (catálogo estable, `UNIQUE(competitor_id, product_name, size_description)`) + `competitor_price_observations` (log particionado de scrapes, FK al catálogo). `product_competitor_matches` referencia el catálogo estable.
- **A3 — Particiones sin automatización + `DEFAULT` catch-all.**
  En 2027 las filas caen en DEFAULT y bloquean crear la partición. **Fix v2:** `ensure_yearly_partition(parent, year)` + job pg_cron/pg_partman; eliminar dependencia de DEFAULT para datos vivos.

### 🟡 Medios
- **M1 —** `product_pricing` usa `COALESCE(store_id,0)` (valor mágico). **Fix v2:** `UNIQUE NULLS NOT DISTINCT` (PG15+).
- **M3 —** `recipe_ingredients.recipe_unit_id ON DELETE SET NULL` altera recetas en silencio. **Fix v2:** `ON DELETE RESTRICT`.
- (M2/M4/M5: estilísticos/documentación, no bloquean — ver §7.)

---

## 3. Diferencias estructurales v1 → v2

| Área | v1 (actual) | v2 (objetivo) |
|------|-------------|---------------|
| Sustitutos | `UNIQUE(orig,sub)` | `EXCLUDE` temporal `no_overlap_isub` |
| Precio actual ingrediente | vista `DISTINCT ON` | columna `ingredients.current_price` + trigger |
| Costo modificador | tabla `product_modifier_costs` | `mv_product_modifier_cost` (matview) |
| Disponibilidad | sin scope check, FK SET NULL | `ck_ia_scope` + FK CASCADE |
| Precio local tienda | `store_ingredient_prices` plano `UNIQUE(store,ing)` | temporal `valid_from/until` + `no_overlap_sip` + `currency_code` |
| Costo ingrediente | disperso en servicios | `fn_ingredient_unit_cost()` (fuente única) |
| Competidores | `competitor_products` particionado (log) | catálogo estable + `competitor_price_observations` particionado |
| `product_competitor_matches` | FK compuesta a scrape `(id, scraped_at)` | FK simple a catálogo `(id)` |
| `product_pricing` unicidad | `COALESCE(store,0)` | `NULLS NOT DISTINCT` |
| `recipe_ingredients` unit FK | `SET NULL` | `RESTRICT` |
| Particiones | fijas + DEFAULT | `ensure_yearly_partition()` sin DEFAULT |

---

## 4. Plan de migración (Alembic, aditivo sobre rev 0004)

Cada paso = una migración independiente. Aplicar en orden. Algunas requieren backfill.

### `0005_substitutes_temporal_exclude` (C1)
```sql
ALTER TABLE ingredient_substitutes DROP CONSTRAINT uq_ingredient_substitutes;
ALTER TABLE ingredient_substitutes ADD CONSTRAINT no_overlap_isub
  EXCLUDE USING gist (
    original_ingredient_id WITH =, substitute_ingredient_id WITH =,
    daterange(valid_from, COALESCE(valid_until,'infinity'::date),'[)') WITH &&);
```

### `0006_ingredient_current_price` (C2)
```sql
ALTER TABLE ingredients ADD COLUMN current_price price_amount;
-- backfill desde el último precio del historial:
UPDATE ingredients i SET current_price = lp.price
FROM (SELECT DISTINCT ON (ingredient_id) ingredient_id, price
      FROM ingredient_price_history ORDER BY ingredient_id, changed_at DESC) lp
WHERE lp.ingredient_id = i.id;
-- trigger sync_ingredient_current_price AFTER INSERT ON ingredient_price_history
-- reemplazar la vista v_current_ingredient_price por la versión O(1)
```

### `0007_modifier_cost_matview` (C4)
```sql
DROP VIEW IF EXISTS v_product_modifier_cost;
DROP TABLE IF EXISTS product_modifier_costs;
CREATE MATERIALIZED VIEW mv_product_modifier_cost AS ...;
CREATE UNIQUE INDEX uq_mv_pmc_modifier ON mv_product_modifier_cost (modifier_id);
```

### `0008_availability_scope_cascade` (C3)
```sql
-- recrear FKs como CASCADE; añadir CHECK de scope
ALTER TABLE ingredient_availability
  DROP CONSTRAINT fk_ia_route, DROP CONSTRAINT fk_ia_region,
  ADD CONSTRAINT fk_ia_route  FOREIGN KEY (supply_route_id) REFERENCES supply_routes(id) ON DELETE CASCADE,
  ADD CONSTRAINT fk_ia_region FOREIGN KEY (region_id)       REFERENCES regions(id)       ON DELETE CASCADE,
  ADD CONSTRAINT ck_ia_scope CHECK (supply_route_id IS NOT NULL OR region_id IS NOT NULL);
```

### `0009_store_ingredient_prices_temporal` (A1)
```sql
ALTER TABLE store_ingredient_prices
  ADD COLUMN currency_code char(3) NOT NULL DEFAULT 'COP'
    REFERENCES currencies(code) ON UPDATE CASCADE ON DELETE RESTRICT,
  ADD COLUMN valid_from date NOT NULL DEFAULT CURRENT_DATE,
  ADD COLUMN valid_until date,
  ALTER COLUMN local_price SET NOT NULL,
  DROP CONSTRAINT uq_store_ingredient_prices,  -- ya no "una por par"
  ADD CONSTRAINT no_overlap_sip EXCLUDE USING gist (
    store_id WITH =, ingredient_id WITH =,
    daterange(valid_from, COALESCE(valid_until,'infinity'::date),'[)') WITH &&);
-- crear fn_ingredient_unit_cost(...)
```
> ⚠️ `store_ingredient_prices` pasa a tener historial. Quitar `updated_at`/trigger (append-only) o mantener según convención.

### `0010_product_pricing_nulls_not_distinct` (M1)
```sql
DROP INDEX uq_product_pricing_current;
CREATE UNIQUE INDEX uq_product_pricing_current
  ON product_pricing (product_id, size_id, store_id, currency_code) NULLS NOT DISTINCT;
```

### `0011_recipe_unit_fk_restrict` (M3)
```sql
ALTER TABLE recipe_ingredients
  DROP CONSTRAINT fk_recipe_ingredients_unit,
  ADD CONSTRAINT fk_recipe_ingredients_unit FOREIGN KEY (recipe_unit_id)
    REFERENCES recipe_units(id) ON DELETE RESTRICT;
```

### `0012_competitor_catalog_split` (A2) — la más invasiva
- Crear `competitor_price_observations` (particionado por `scraped_at`).
- Reestructurar `competitor_products` a catálogo estable (id no compuesto).
- Migrar matches a FK simple.
- **Requiere backfill** desde los scrapes existentes (deduplicar por competidor+nombre+talla). En greenfield (datos de prueba) se puede truncar y recrear.

### `0013_partition_automation` (A3)
- `CREATE FUNCTION ensure_yearly_partition(...)`.
- Crear particiones 2025-2027 explícitas; programar pg_cron para años futuros.

> **Límite Alembic:** los `revision id` deben ser ≤ 32 chars (`alembic_version.version_num varchar(32)`). Mantener ids cortos.

---

## 5. Cambios de código por etapa

### Modelos (`backend/models/`)
- `ingredient.py`: añadir `current_price = Column(Numeric(14,4))`.
- `supply_chain.py`:
  - `IngredientSubstitute`: quitar `UniqueConstraint(orig,sub)` (el EXCLUDE va en migración; documentar).
  - `IngredientAvailability`: añadir `CheckConstraint` scope; FKs `ondelete="CASCADE"`.
- `modifier.py`: **eliminar** `ProductModifierCost` (pasa a matview, sin modelo o con modelo `__table__` de solo-lectura).
- `store.py`: `StoreIngredientPrice` → añadir `currency_code`, `valid_from`, `valid_until`, `local_price` NOT NULL; quitar UNIQUE.
- `product.py`: `RecipeIngredient.recipe_unit_id` FK `ondelete="RESTRICT"`.
- `competitor.py`: rehacer — `CompetitorProduct` catálogo estable (PK simple), nuevo `CompetitorPriceObservation` (PK compuesta particionada), `ProductCompetitorMatch` FK simple a catálogo (quitar `competitor_product_scraped_at` y la `ForeignKeyConstraint` compuesta).
- `__init__.py`: actualizar exports (quitar `ProductModifierCost`, añadir `CompetitorPriceObservation`).

### Schemas (`backend/schemas/`)
- `ingredient.py`: `current_price` en Response (read-only).
- `competitor.py`: separar schemas catálogo vs observación.
- `store.py`: `StoreIngredientPrice*` con `currency_code`, `valid_from/until`.

### Routers (`backend/routers/`)
- `competitors.py` / `competitors_ui.py`: CRUD catálogo + endpoint de observaciones; matches contra catálogo.
- nuevo o ajustar: endpoint que use `fn_ingredient_unit_cost`.

### Servicios (`backend/services/`)
- `cost_calculator.py`: usar `fn_ingredient_unit_cost` (fuente única) en vez de lógica dispersa de precio local/ruta; leer `ingredients.current_price`.
- `pricing_engine.py`: usar `mv_product_modifier_cost`.
- `scraping/scraper_manager.py`: escribir en `competitor_price_observations` + upsert de catálogo `competitor_products`; mantener INSERT a `ingredient_price_history` (el trigger sincroniza `current_price`).
- `report_generator.py`: ajustar queries de benchmark competidor al nuevo split.

### Refresco de matviews
- Programar `REFRESH MATERIALIZED VIEW CONCURRENTLY mv_product_modifier_cost` tras cambios de precio/efectos (pg_cron o post-commit en el servicio de pricing).

---

## 6. Tests (`backend/tests/`)
- `conftest.py`: ya tiene `seed_categories` autouse. Añadir fixtures para catálogo competidor + observación; ajustar fixtures de `StoreIngredientPrice` (nuevos campos).
- Tests nuevos:
  - `test_substitute_temporal.py`: cerrar+reinsertar mismo par (debe pasar); solapamiento (debe fallar EXCLUDE).
  - `test_ingredient_unit_cost.py`: precedencia local→ruta→catálogo.
  - `test_current_price_trigger.py`: INSERT en historial actualiza `ingredients.current_price`.
  - `test_competitor_catalog.py`: catálogo estable + observaciones + match persistente tras re-scrape.
- Actualizar: `test_reports.py` (benchmark competidor), `test_cost_calculator.py` (fuente de precio).
- La suite corre contra Supabase real con rollback por test (conftest). Lenta; correr por archivo con timeout si hace falta.

---

## 7. Decisiones abiertas / fuera de alcance
- **M2** (dominio `iso_currency`): la FK a `currencies` ya basta; opcional.
- **M4** (categoría sin margen → default silencioso): documentar si es intencional.
- **M5** (`metadata` JSONB sin esquema): mantener disciplina; GIN solo si se filtra por contenido; columna cuando el atributo madure.
- **labor_cost_per_minute** por producto: posible config global/tienda; no urgente.

---

## 8. Orden recomendado de ejecución
1. Cerrar deuda v1: aplicar `0004`, suite verde.
2. Migraciones baratas y de bajo riesgo primero: `0005` (C1), `0010` (M1), `0011` (M3), `0008` (C3).
3. C2/C4 (`0006`, `0007`) + ajustes de servicios/matview.
4. A1 (`0009`) + `fn_ingredient_unit_cost` + `cost_calculator`.
5. A2 (`0012`) — la más invasiva (modelos + scraper + reports). En greenfield, truncar+recrear competidores.
6. A3 (`0013`) + pg_cron.
7. Commit por etapa; cada etapa con tests verdes antes de avanzar.

---

## 9. Referencias
- DDL objetivo v2: `files/schema_v2_refactorizado.sql`
- DDL v1 validado: `files/schema_refactorizado.sql`
- Plan refactor v1: `REFACTOR_PLAN.md`
- Modelo de negocio supply chain: `CLAUDE.md`
- Análisis original: `files/analisis_y_refactor_qargo.md`, `files/qargo_erd.mermaid`
