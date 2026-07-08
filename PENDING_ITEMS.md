# Pending items — technical debt

## `_create_ingredient` no crea ruta de suministro para ingredientes nuevos del sync

**Fecha:** 2026-07-07
**Ubicación:** `backend/services/catalog_sync.py` → `CatalogSyncService._create_ingredient`
**Severidad:** no bloqueante para el flujo de mapeo manual; sí bloqueante para que los
ingredientes nuevos participen en el cálculo de costos.

### Qué pasa

`_create_ingredient` inserta únicamente una fila en `ingredients`
(name, category, purchase_unit, purchase_price, current_price). **No** crea:

- `ingredient_supplier_refs` — vínculo al distribuidor de la API (external_name,
  external_code = SKU del catálogo, purchase_unit).
- `supply_routes` — la ruta fabricante/distribuidor → ingrediente.
- `supply_route_prices` — el precio resoluble por `fn_resolve_supply_route`.

Verificado en prod (pre-check 2 del flujo de mapeo): de los 236 ingredientes
pendientes creados por el sync, **cero** tienen filas en `ingredient_supplier_refs`
o `supply_routes`.

### Consecuencia

Un ingrediente confirmado como **"Keep as new"** queda activo pero **sin ruta de
suministro** → sin precio resoluble para el motor de costos. Mismo problema que los
133 placeholders ya documentados: existen en `ingredients` pero no participan en el
pipeline de costos porque no hay `supply_route` ni `supply_route_prices` vigente.

El flujo de mapeo manual **sí** funciona correctamente hoy: las reasignaciones
(`ingredient_supplier_refs`, `supply_routes`, etc.) son no-ops sobre tablas vacías y
no fallan. El problema es aguas abajo, en "Keep as new".

### Qué hacer

`_create_ingredient` debe, además de insertar en `ingredients`:

1. Crear un `supply_route` para el ingrediente, vinculado al distribuidor de la API
   (o `is_direct` según corresponda).
2. Crear un `ingredient_supplier_ref` (external_name = nombre del catálogo,
   external_code = SKU, purchase_unit, supply_route_id) — el sync ya tiene estos datos
   del item de la API.
3. Escribir el precio inicial en `supply_route_prices` (list_price = qargo_price =
   unit_price, currency, valid_from = hoy) en lugar de sólo mirror en
   `ingredients.purchase_price`.

Con eso, un ingrediente confirmado "Keep as new" queda inmediatamente resoluble por
`fn_resolve_supply_route` y participa en el cálculo de costos.

### Nota sobre el distribuidor

El distribuidor de la API no está persistido hoy para los items auto-creados (por eso
la card de review muestra "—" en distribuidor). Resolver esta deuda también habilita
mostrar el distribuidor real en la card.
