# Plan final de implementación — Motor de cálculo + integración de Proveedores

> Documento de implementación para Claude Code. Une dos frentes:
> **(A)** corregir las vulnerabilidades/hallazgos del motor de cálculo
> (análisis en `files/calc_engine_redesign.md`), y
> **(B)** extender la lógica para que **proveedores / cadena de suministro**
> sean ciudadanos de primera clase en el pipeline de costos (hoy ignorados por
> el motor).
> Schema base: v3 (head `0020`). Motor: `backend/services/cost_calculator.py`,
> `backend/services/pricing_engine.py`.

---

## 0. Estado actual y brecha

**Lo que YA existe (schema v3):** `supply_routes`, `supply_route_assignments`
(temporal + prioridad), `supply_route_prices` (list/qargo + moneda + `price_unit_id`),
`ingredient_supplier_refs` (nombre/código/unidad de compra por ruta),
`supplier_unit_conversions` (unidad de compra proveedor → unidad de receta),
`ingredient_availability`, `ingredient_substitutes` (+ regiones), `store_supplier_history`,
y las funciones `fn_resolve_supply_route`, `fn_ingredient_unit_cost`.

**La brecha:** el **motor de cálculo casi no usa nada de eso**.
- Solo consulta `fn_ingredient_unit_cost` (precio escalar) y **solo si hay `store_id`**.
- Ignora `supplier_unit_conversions`: divide por `ingredient.conversion_factor`
  (unidad de catálogo) aunque el precio venga de un proveedor con empaque distinto
  → **costo incorrecto** cuando difieren.
- No registra **procedencia** (qué ruta/fabricante/distribuidor) en el costo ni en snapshots.
- No consume `ingredient_availability` ni `ingredient_substitutes`: en
  desabastecimiento, sigue costeando el ingrediente original (la lógica de
  sustitución está modelada pero **muerta**).
- No escribe `store_supplier_history` (qué ruta usó cada tienda).
- `recipe_cost_snapshots` nunca se escribe → sin trazabilidad.

---

## 1. Vulnerabilidades del motor a corregir (Frente A)

| ID | Severidad | Hallazgo | Fix |
|----|-----------|----------|-----|
| E1 | 🔴 | Sub-recetas sin memo → reexpansión exponencial | DFS post-orden memoizado sobre el DAG (O(V+E)) |
| E2 | 🔴 | N+1: `_get_recipe_unit_conversion` y `fn_ingredient_unit_cost` por ingrediente | Precarga bulk + resolución de precio **set-based** |
| E3 | 🟠 | `_calculate_labor_cost` re-consulta el producto ya cargado | Reusar entidad precargada |
| E4 | 🟠 | `get_cost_breakdown` recomputa y devuelve detalle vacío (TODOs) | Construir desglose línea-a-línea desde el cómputo único |
| E5 | 🟠 | Recompute completo por talla | Costo base una vez (fijo + escalable), escalar por talla |
| E6 | 🟢 | Redondeo fijo a 2 dec ignora `currencies.minor_unit` | `quantize` según moneda |
| E8 | 🟡 | Ciclos por profundidad (>10), número mágico | `set` visiting + trigger BD V3-8 (ya existe) |
| T1 | 🔴 | `recipe_cost_snapshots` nunca escrito | Persistir snapshot por cálculo |
| T2 | 🔴 | Sin versión de fórmula / lineage | `formula_version` + precios `valid_from` usados en snapshot |
| T3 | 🟠 | Batch sin checkpoint/retry | Commit por chunk + reintento idempotente |

Base técnica: el rediseño "prefetch → compute puro memoizado → persist" de
`files/calc_engine_redesign.md`.

---

## 2. Expansión de Proveedores (Frente B)

Hacer del **sourcing** (ruta+precio+unidad+procedencia) la fuente del costo, y
activar disponibilidad/sustitutos.

### B1 — Resolver sourcing completo, no solo precio escalar
Nueva función SQL **única fuente de verdad** que devuelve la tupla completa:

```
fn_resolve_ingredient_sourcing(p_ingredient_id, p_store_id, p_date)
  RETURNS (
    supply_route_id, manufacturer_id, distributor_id, is_direct,
    unit_price,            -- qargo_price del precio vigente de la ruta (o fallback)
    price_currency,
    purchase_qty, recipe_qty,   -- de supplier_unit_conversions (unidad correcta)
    source                 -- 'local' | 'route' | 'catalog'
  )
```
El motor usa `recipe_qty/purchase_qty` (conversión **del proveedor**) en vez de
`ingredient.conversion_factor` cuando el precio viene de una ruta. Corrige V3-5
+ el costo por unidad incorrecto.

### B2 — Disponibilidad → sustitución automática
Nueva función:
```
fn_active_substitute(p_ingredient_id, p_store_id, p_date)
  RETURNS (substitute_ingredient_id, quantity_ratio, cost_impact_pct) | NULL
```
Lógica: si `ingredient_availability` (por ruta resuelta o región de la tienda)
tiene `status IN ('shortage','discontinued','seasonal')` y existe
`ingredient_substitutes` aprobado con `activation_condition` compatible y
vigente, devolver el sustituto. El motor, al costear una línea:
1. Resolver disponibilidad del ingrediente original.
2. Si no disponible → buscar sustituto activo → costear el **sustituto** con
   `quantity_ratio` aplicado, marcar `has_substitutes=true`.
3. Si no hay sustituto → política configurable (error / costo 0 / usar original).

### B3 — Procedencia y lineage en el snapshot
`recipe_cost_snapshots.snapshot_detail` (JSONB) por línea:
```json
{ "ingredient_id":1, "supply_route_id":5, "manufacturer_id":3, "distributor_id":null,
  "is_substitute":false, "original_ingredient_id":null,
  "unit_price":4500, "currency":"COP", "qty":252.63, "line_cost":1136.84,
  "source":"route", "price_valid_from":"2026-01-01" }
```
+ campos top-level `formula_version`, `triggered_by`, `has_substitutes`.

### B4 — Registrar `store_supplier_history`
Cuando un cálculo (o un proceso de asignación) determina la ruta vigente de una
tienda+ingrediente, registrar/abrir la fila en `store_supplier_history`
(patrón close+insert) para auditar qué proveedor usó cada tienda y desde cuándo.

### B5 — Ingesta de precios de proveedor (pipeline)
Hoy `supply_route_prices` se cargan a mano por router. Añadir un servicio de
**ingesta** (batch/CSV o API) que aplique el patrón temporal (cerrar precio
vigente + insertar nuevo) y valide `qargo_price <= list_price` + moneda.

---

## 3. Pipeline de datos objetivo (completo)

```
INGESTA           proveedores: supply_route_prices (ingesta B5), supplier refs,
                  supplier_unit_conversions, availability; recetas; tallas
   ↓ (precarga bulk, set-based)
RESOLUCIÓN        por (ingrediente, tienda, fecha):
                  fn_resolve_ingredient_sourcing  → ruta+precio+unidad+procedencia
                  fn_active_substitute            → sustituto si no disponible
   ↓
FÓRMULAS          unit_cost = unit_price / (recipe_qty/purchase_qty del proveedor)
                  × qty (conv receta) × scale × 1/yield × 1/proceso × quantity_ratio
   ↓ (memo DAG O(V+E))
CONSOLIDACIÓN     Σ ingredientes + sub-recetas + empaque + labor → quantize(moneda)
   ↓
PERSISTENCIA      product_pricing (upsert) + product_price_history (cambios)
                  + recipe_cost_snapshots (lineage) + store_supplier_history
```

---

## 4. Fases de implementación

> Cada fase: migración(es) si aplica → código → tests verdes → commit. Orden estricto.

### Fase 1 — Refactor del motor (Frente A, sin proveedores nuevos)
- **Código:** reescribir `cost_calculator` al patrón prefetch→compute memoizado.
  - `load_context()` (bulk queries), `BaseCost(fixed, scalable)`, `base_recipe_cost()` memo, `cost_for_size()` con `quantize` por moneda.
  - Implementar el desglose real en `get_cost_breakdown` (E4).
  - Corregir E3 (reusar producto), E5 (escalar), E6 (moneda), E8 (visiting set).
- **Tests:** los actuales deben seguir verdes (mismos números); + tests de memo (sub-receta compartida se valúa una vez), + redondeo por moneda.
- **Criterio:** suite verde; 0 N+1 en el hot loop (verificar con conteo de queries).

### Fase 2 — Sourcing de proveedor en el costo (B1)
- **Migración 0021:** `fn_resolve_ingredient_sourcing` + (opcional) vista
  `v_ingredient_effective_sourcing` para precarga batch.
- **Código:** `load_context` usa la vista/función para el mapa de precio+unidad+procedencia;
  `_line_cost` usa la conversión del proveedor cuando `source='route'`.
- **Tests:** ingrediente con ruta cuyo empaque difiere del catálogo → costo usa
  conversión del proveedor; fallback a catálogo sin ruta.
- **Criterio:** costo correcto bajo proveedor con unidad distinta; procedencia disponible.

### Fase 3 — Disponibilidad y sustitutos (B2)
- **Migración 0022:** `fn_active_substitute`.
- **Código:** en `_line_cost`/resolución de línea, consultar disponibilidad →
  sustituir si aplica (quantity_ratio, marcar `has_substitutes`). Política
  configurable cuando no hay sustituto.
- **Tests:** shortage + sustituto aprobado vigente → costea sustituto con ratio;
  shortage sin sustituto → política; `always`/`unavailable`/`shortage` respetadas;
  regiones (`ingredient_substitute_regions`).
- **Criterio:** sustitución automática correcta y auditable.

### Fase 4 — Trazabilidad + historial de proveedor (T1/T2/B3/B4)
- **Código:** persistir `recipe_cost_snapshots` con `snapshot_detail` (lineage
  por línea incl. ruta/proveedor/sustituto/fx) + `formula_version`; escribir
  `store_supplier_history` (close+insert) al fijar ruta por tienda.
- **Tests:** snapshot inmutable con desglose completo; historial de proveedor
  sin solapamiento (EXCLUDE ya existe).
- **Criterio:** todo costo reconstruible desde su snapshot.

### Fase 5 — Concurrencia + ingesta (T3/B5)
- **Código:** `recompute_all` con `ProcessPoolExecutor`, sesión por worker,
  commit+checkpoint por chunk, reintento idempotente; servicio de ingesta de
  `supply_route_prices` (temporal, validado); evento `on_price_change` →
  `reverse_bom_closure` → recálculo incremental.
- **Tests:** batch reanudable; ingesta cierra+inserta; incremental recalcula solo
  afectados.
- **Criterio:** batch a gran volumen sin saturar; recálculo incremental por evento.

---

## 5. Migraciones nuevas previstas
- `0021_fn_resolve_ingredient_sourcing` (B1) — id ≤32 chars: usar `0021_fn_sourcing`.
- `0022_fn_active_substitute` (B2).
- (Opcional) `0023_sourcing_view` si se materializa la precarga.
> Multi-moneda: usar `fn_convert_amount` (v3) para normalizar precios de proveedor
> en distinta moneda a la moneda de la tienda antes de sumar.

## 6. Decisiones (resueltas)
- **Política sin-sustituto** en shortage → **costear el ingrediente original igual**
  + flag de advertencia en el snapshot (`unavailable_no_substitute: true`). No
  bloquea el cálculo.
- **Conversión proveedor vs catálogo** → **proveedor gana si `source='route'`**;
  si el precio es de catálogo, usar `ingredient.conversion_factor`. Cada precio
  con su unidad.
- **`fn_active_substitute`** → limitar a **1 nivel** de sustitución (si el
  sustituto también está en shortage, aplicar la política sin-sustituto sobre él).

Riesgos:
- **Costo de cambio:** Fase 1 reescribe el motor → riesgo de regresión numérica;
  mitigar con los tests existentes como red de seguridad (deben dar idénticos).

## 7. Referencias
- Análisis del motor: `files/calc_engine_redesign.md`
- Schema v3: `files/schema_v3_deltas.sql` + migraciones 0001-0020
- Modelo de negocio supply chain: `CLAUDE.md` (§11 fn_resolve, §15 sustitutos futuros)
