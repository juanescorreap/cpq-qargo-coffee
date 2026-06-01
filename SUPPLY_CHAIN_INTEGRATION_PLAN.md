# Plan de integración: supply chain → calculadora de costos

> **Contexto permanente para Claude Code.** Este documento describe el plan completo para conectar
> el modelo de cadena de suministro (Fases 1–6 del schema, implementadas en junio 2026) con el
> frontend y la calculadora de costos. Cada fase es independiente y reversible. Implementar en orden.

---

## Estado actual del pipeline (punto de partida)

```
Usuario selecciona producto + tienda
        ↓
CostCalculator._get_ingredient_price(ingredient_id, store_id)
        ↓
  ┌─ StoreIngredientPrice.local_price   (si existe override manual)
  └─ Ingredient.purchase_price          (fallback global)
        ↓
cost_per_usage_unit = price / conversion_factor
        ↓
ProductPricing / product_price_history
```

Las tablas del nuevo modelo (`supply_routes`, `supply_route_prices`, `fn_resolve_supply_route`, etc.)
están en la BD pero el pipeline no las consulta. Son datos muertos hasta la Fase D.

---

## Árbol de dependencias

```
regions ──────────────────────────────────────────────────┐
manufacturers + distributors ──┐                          │
                               ↓                          │
                         supply_routes                    │
                               │                          │
                   supply_route_prices         stores.region_id
                   ingredient_supplier_refs         │
                   supplier_unit_conversions         │
                               │                    │
                         supply_route_assignments ──┘
                               │
                    fn_resolve_supply_route(ingredient_id, store_id)
                               │
                    CostCalculator (nuevo precio por ruta)
                               │
                    product_pricing / UI
```

---

## Fase A — Schemas y routers API

**Estado:** pendiente

### Qué se hace

1. **`backend/schemas/supply_chain.py`** — Pydantic schemas para todas las tablas nuevas:
   - `RegionCreate / RegionResponse`
   - `ManufacturerCreate / ManufacturerResponse`
   - `DistributorCreate / DistributorResponse`
   - `SupplyRouteCreate / SupplyRouteResponse` (con `ingredient_name` expandido)
   - `SupplyRouteAssignmentCreate / SupplyRouteAssignmentResponse`
   - `SupplyRoutePriceCreate / SupplyRoutePriceResponse`
   - `IngredientSupplierRefCreate / IngredientSupplierRefResponse`

2. **`backend/schemas/store.py`** — agregar `region_id` y `default_currency_code` a `StoreResponse` y `StoreUpdate`.

3. **`backend/schemas/cost.py`** — agregar a `IngredientCostDetail`:
   - `supply_route_id: Optional[int]`
   - `route_scope: Optional[str]`  — `"store_override"` | `"region_default"` | `"base_price"`
   - `price_source: str`  — `"route"` | `"store_override"` | `"base"`

4. **Nuevos routers API** (solo JSON, sin HTML):
   - `backend/routers/regions.py` — CRUD completo
   - `backend/routers/manufacturers.py` — CRUD
   - `backend/routers/distributors.py` — CRUD
   - `backend/routers/supply_routes.py` — CRUD + endpoint `GET /{id}/active-price`
   - `backend/routers/supply_route_assignments.py` — crear asignación, cerrar asignación (`valid_until = today`)
   - `backend/routers/supply_route_prices.py` — crear precio (cierra el vigente + inserta nuevo), historial
   - `backend/routers/supply_chain.py` — endpoint utilitario `GET /resolve-route?ingredient_id=X&store_id=Y`
     (llama `fn_resolve_supply_route`, útil para debug y para el UI de rutas activas en tiendas)

5. Registrar todos en `backend/main.py`.

### Por qué primero
El UI admin de la Fase B consume estos endpoints. Sin ellos no hay forma de cargar datos en las nuevas tablas.

---

## Fase B — UI admin de cadena de suministro

**Estado:** pendiente  
**Depende de:** Fase A

### Qué se hace

1. **`backend/routers/supply_chain_ui.py`** — rutas HTML bajo `/supply-chain/`

2. **Templates en `backend/templates/supply_chain/`**:

   - `regions/list.html` — tabla de regiones (código, nombre, país). Formulario HTMX para crear/editar inline.
   - `manufacturers/list.html` — tabla con nombre, NIT, país.
   - `distributors/list.html` — tabla con nombre, email, teléfono.
   - `routes/list.html` — tabla por ingrediente: ingrediente | fabricante | distribuidor | compra directa | activo.
     Formulario para crear ruta.
   - `routes/detail.html` — detalle de una ruta con tres tabs:
     - **Precio**: historial + formulario para nuevo precio (cierra el vigente automáticamente).
     - **Referencias**: `ingredient_supplier_refs` — nombre externo, código SKU, unidad de compra.
     - **Conversiones**: `supplier_unit_conversions` — unidad de compra → unidad de receta.
   - `assignments/list.html` — asignaciones vigentes agrupadas por región. Formulario para asignar ruta
     a región o tienda con prioridad y `valid_from`. Botón "cerrar asignación" con campo motivo.

3. **Navbar** — agregar "Cadena de suministro" con desplegable:
   Regiones / Fabricantes / Distribuidores / Rutas / Asignaciones.

### Por qué antes del calculador
Sin este UI los operadores no pueden cargar los datos de regiones, fabricantes, distribuidores,
rutas y precios. El calculador necesita datos reales para producir resultados distintos a los actuales.

---

## Fase C — Integración de regiones en tiendas

**Estado:** pendiente  
**Depende de:** Fase A (para el dropdown de regiones)

### Qué se hace

1. **`backend/routers/stores_ui.py`** — en la vista de detalle de tienda:
   - Campo "Región" en la tarjeta de info con dropdown de regiones activas.
   - Endpoint HTMX `PATCH /{store_id}/region` para asignar/cambiar región sin recargar página.

2. **`backend/routers/stores.py`** — actualizar `PUT /{store_id}` para aceptar `region_id` y `default_currency_code`.

3. **`backend/templates/stores/detail.html`**:
   - Mostrar región asignada en la tarjeta de info.
   - Si no tiene región: advertencia visible — *"Esta tienda no tiene región asignada — los costos
     usarán precios base globales."*
   - **Nuevo tab "Rutas activas"**: tabla de solo lectura que muestra por cada ingrediente (de cualquier
     receta activa de la tienda) el resultado de `fn_resolve_supply_route`:
     ingrediente | scope (regional / override) | fabricante/distribuidor | precio vigente.
     Se construye llamando al endpoint `/api/supply-chain/resolve-route` del router utilitario.

### Por qué aquí
Sin `stores.region_id` asignado, `fn_resolve_supply_route` solo devuelve overrides directos de tienda
(que no existen aún). La asignación de región es el switch que activa el nuevo modelo de resolución.

---

## Fase D — Refactor del calculador de costos

**Estado:** pendiente  
**Depende de:** Fases A, B, C (datos deben existir en la BD para que produzca resultados distintos)

Esta es la fase central. Cambia la lógica de `CostCalculator._get_ingredient_price()`.

### Qué se hace

1. **Nuevo método privado** `_resolve_route_price(ingredient_id, store_id, db)`:

   ```
   1. Llamar fn_resolve_supply_route(ingredient_id, store_id) via SQL raw
   2. Si retorna fila:
      a. Buscar supply_route_prices vigente (valid_until IS NULL) para supply_route_id
      b. Buscar supplier_unit_conversions para la ref de esa ruta (si existe)
      c. Retornar (qargo_price, supply_route_id, scope, conversion_override_if_any)
   3. Si no retorna fila → retornar None (caer al siguiente nivel)
   ```

2. **Actualizar `_get_ingredient_price()`** — nueva jerarquía de resolución:

   ```
   Prioridad 1: supply route price vía fn_resolve_supply_route
                (usa supplier_unit_conversions de la ruta si existe;
                 si no, usa Ingredient.conversion_factor como siempre)
   Prioridad 2: StoreIngredientPrice.local_price
                (override manual legacy, para retrocompatibilidad)
   Prioridad 3: Ingredient.purchase_price
                (fallback global — comportamiento actual preservado)
   ```

3. **Pre-fetch eficiente**: El calculador ya hace pre-fetch de `StoreIngredientPrice` en bulk.
   Extender para traer en una sola query SQL las rutas resueltas via `fn_resolve_supply_route`
   para todos los ingredientes del producto + `store_id` antes de iterar.

4. **Actualizar `get_cost_breakdown()`**: propagar `supply_route_id`, `route_scope`, `price_source`
   al `IngredientCostDetail` de cada ingrediente.

5. **Manejo de sustitutos** (subtarea dentro de esta fase):
   - Consultar `ingredient_availability` por `ingredient_id` + ruta resuelta.
   - Si hay `status = 'shortage'` vigente, buscar `ingredient_substitutes` con
     `activation_condition IN ('shortage', 'unavailable')`.
   - Si hay sustituto activo: usar `substitute_ingredient_id` para resolver precio,
     aplicar `quantity_ratio`, agregar al breakdown con `is_substitute = True`.
   - Si no hay sustituto disponible: continuar con el ingrediente original
     (el shortage no bloquea el cálculo).

### Riesgo a mitigar
Si no hay ruta asignada para un ingrediente/tienda, el calculador **no debe romper** —
debe caer silenciosamente al comportamiento actual (StoreIngredientPrice → purchase_price).
El fallback debe estar cubierto por tests antes de desplegar.

---

## Fase E — Enriquecimiento del UI de costos

**Estado:** pendiente  
**Depende de:** Fase D

### Qué se hace

1. **`backend/templates/costs/_result.html`** — en la tabla de ingredientes:
   - Columna **Fuente de precio** con badge de color según `price_source`:
     - Verde: `"route"` — "Ruta regional" o "Override tienda" (nuevo modelo activo)
     - Amarillo: `"store_override"` — "Override manual" (StoreIngredientPrice legacy)
     - Gris: `"base"` — "Precio base" (Ingredient.purchase_price)
   - Columna **Proveedor**: nombre del distribuidor o fabricante de la ruta
     (join desde `supply_route_id` → manufacturers/distributors).

2. Si hay sustitutos activos en el breakdown: mostrar alerta en el resultado —
   *"X ingredientes están siendo sustituidos"* — con detalle expandible por sustituto.

3. **`backend/templates/costs/calculator.html`** — al seleccionar tienda (HTMX):
   si la tienda no tiene región asignada, mostrar aviso inline:
   *"Esta tienda no tiene región — el costo usa precios base. Asigna una región en configuración."*

---

## Tabla resumen

| Fase | Archivos principales | Impacto en pipeline | Rompe comportamiento actual |
|---|---|---|---|
| A — API routers | 7 nuevos routers, 1 schema nuevo | Solo agrega endpoints | No |
| B — Admin UI | 1 router UI, ~8 templates | Solo agrega páginas | No |
| C — Tiendas + regiones | `stores_ui.py`, `stores.py`, `detail.html` | Permite asignar región; sin asignar, igual | No |
| D — Calculador | `cost_calculator.py` | **Cambia precios calculados** si hay rutas activas | Solo si hay datos cargados |
| E — UI de costos | `_result.html`, `calculator.html` | Visual solamente | No |

---

## Fuera de scope (intencionalmente)

| Concepto | Por qué no ahora |
|---|---|
| ETL/ingesta masiva de rutas desde Excel o facturas | Proyecto separado de migración de datos |
| Modelo predictivo de desabastecimiento | `ingredient_availability` se registra pero no se procesa; ver sección 15 de CLAUDE.md |
| Scoring de proveedores | `metadata JSONB` disponible pero sin proceso formal aún |
| Precios por volumen (`volume_tiers`) | No modelado; ver sección 15 de CLAUDE.md |
| Compra consolidada entre tiendas | Idem |

---

## Archivos clave de referencia

| Qué | Dónde |
|---|---|
| Calculador actual | `backend/services/cost_calculator.py` |
| Pricing engine | `backend/services/pricing_engine.py` |
| Rutas de costos (API) | `backend/routers/costs.py` |
| Rutas de costos (UI) | `backend/routers/costs_ui.py` |
| Rutas de tiendas (API) | `backend/routers/stores.py` |
| Rutas de tiendas (UI) | `backend/routers/stores_ui.py` |
| Modelos supply chain | `backend/models/supply_chain.py` |
| Schema del plan | `CLAUDE.md` (secciones 5–11) |
| Migraciones ejecutadas | `backend/migrations/versions/a1b2c3d4e5f6_*` → `f6a1b2c3d4e5_*` |
