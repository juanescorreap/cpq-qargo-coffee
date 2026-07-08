# Plan Edinburgo — Producción con costos reales

> Documento para Claude Code. Lee esto completo antes de escribir
> cualquier línea de código. Implementar en el orden exacto de los
> 5 pasos — no saltar ni reordenar.

---

## Contexto

Objetivo: tener costos reales y confiables para la tienda de Edinburgo
(store_id=519, code=7-ED-TX) corriendo en producción en Railway.

Estado actual de los 164 ingredientes usados en recetas activas:
- A (precio vía ruta activa): 12 ingredientes (7.3%)
- B (precio fresco API, sin ruta): 7 ingredientes (4.3%)
- C (precio fallback Excel, posiblemente desactualizado): 145 (88.4%)
- D (sin precio): 0

El motor puede correr hoy sin $0, pero el 88% usa precios del Excel
de carga original. El objetivo es limpiar eso antes de correr el motor
para que el resultado sea confiable desde la primera corrida.

---

## Paso 1 — Deploy a Railway

### Qué hacer

1. Verificar que la migración 0033 (tablas de catalog sync:
   `store_catalog_mapping`, `catalog_sync_log`, `catalog_match_log`)
   está incluida en el deploy. Si no existe como archivo Alembic,
   crearla ahora antes del deploy.
2. `git push` al branch que Railway monitorea (confirma cuál es
   leyendo el archivo de configuración de Railway o el Procfile).
3. Verificar que las variables de entorno necesarias están en Railway:
   - `CATALOG_API_BASE_URL`
   - `CATALOG_API_EMAIL`
   - `CATALOG_API_PASSWORD`
   - `CATALOG_SYNC_SCHEDULE`
   Si alguna falta, reportarlo — no asumir que están.

### Verificación post-deploy

Confirmar que estas URLs cargan sin error en producción:
- `/admin/catalog-sync`
- `/pricing/overview`
- `/stores/519/pricing-overview`
- `/admin/catalog-sync/new-ingredients` (la Sección 4 con pending review)

Si cualquiera de estas falla en producción pero funciona en local,
reportar el error exacto antes de continuar.

---

## Paso 2 — Pantalla de corrección de precios: /admin/price-review

### Propósito

Pantalla dedicada para revisar y corregir los precios de los 145
ingredientes del estado C de Edinburgo (precio fallback del Excel,
posiblemente desactualizado) de forma eficiente con edición inline.

### Archivos a crear

```
backend/routers/price_review_ui.py
templates/admin/price_review/
    overview.html           ← página completa
    _ingredient_row.html    ← fila individual (partial HTMX)
    _edit_form.html         ← formulario inline de edición
```

### Query para poblar la pantalla

```sql
-- Los 145 ingredientes del estado C para Edinburgo:
-- tienen purchase_price pero NO tienen supply_route activa con precio
-- y NO aparecieron en el sync de Edinburgo como 'updated'

SELECT
    i.id,
    i.name,
    i.purchase_price,
    i.purchase_unit,
    i.canonical_unit,
    c.display_name  AS category_name,
    -- Flag de precio sospechoso para alertar al usuario
    CASE
        WHEN i.name = 'Ice Cubes' AND i.purchase_price < 0.10
            THEN 'suspicious_low'
        WHEN i.purchase_price > 200 AND i.purchase_unit ILIKE '%L%'
            THEN 'suspicious_high'
        WHEN i.purchase_price > 100 AND i.purchase_unit ILIKE '%unit%'
            THEN 'suspicious_unit'
        ELSE 'ok'
    END AS price_flag,
    -- Estado de revisión (se guarda en metadata JSONB del ingrediente,
    -- o en una tabla nueva price_review_status — ver abajo)
    COALESCE(prs.status, 'pending') AS review_status
FROM ingredients i
LEFT JOIN categories c ON c.slug = i.category
LEFT JOIN price_review_status prs
    ON prs.ingredient_id = i.id
    AND prs.store_id = 519
WHERE i.is_active = true
  AND i.purchase_price IS NOT NULL
  -- Estado C: no tiene supply_route activa con precio
  AND NOT EXISTS (
      SELECT 1 FROM supply_routes sr
      JOIN supply_route_prices srp ON srp.supply_route_id = sr.id
      WHERE sr.ingredient_id = i.id
        AND sr.is_active = true
        AND srp.valid_until IS NULL
  )
  -- Excluir los que la API de Edinburgo ya actualizó (estado B)
  AND i.id NOT IN (
      SELECT DISTINCT cml.matched_ingredient_id
      FROM catalog_match_log cml
      JOIN catalog_sync_log csl ON csl.id = cml.sync_log_id
      WHERE csl.store_id = 519
        AND cml.action_taken IN ('created', 'updated')
  )
ORDER BY c.display_name, i.name;
```

### Tabla nueva: price_review_status

```sql
CREATE TABLE public.price_review_status (
    id              SERIAL PRIMARY KEY,
    ingredient_id   INTEGER NOT NULL REFERENCES ingredients(id),
    store_id        INTEGER NOT NULL REFERENCES stores(id),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    -- 'pending' | 'reviewed' | 'skipped'
    reviewed_by     VARCHAR(100),
    reviewed_at     TIMESTAMPTZ,
    notes           TEXT,
    UNIQUE (ingredient_id, store_id)
);
```

Esta tabla registra el progreso de revisión sin tocar la tabla de
ingredientes. Permite retomar la revisión si se interrumpe.

### Diseño de la pantalla

**Header:**
```
Price Review — Edinburg (TX)
145 ingredients with fallback pricing

Progress: [████████░░░░░░░░] 45 / 145 reviewed
[Filter: All ▼] [Category: All ▼] [Show: Pending / Reviewed / All]
```

**Tabla con una fila por ingrediente, agrupada por categoría:**

| Ingredient | Current Price | Unit | Flag | Status | Action |
|---|---|---|---|---|---|
| Espresso | $188.16 | Bag 5 lb | — | Pending | Edit / Skip |
| Vanilla Syrup | $222.97 | 1 L | ⚠ High | Pending | Edit / Skip |
| Ice Cubes | $0.01 | 1 gal | ⚠ Low | Pending | Edit / Skip |

**Badges de flag:**
- ⚠ High → precio sospechosamente alto para la unidad
- ⚠ Low → precio sospechosamente bajo (como $0.01)
- ⚠ Unit → precio alto con unidad "unit" (puede ser confusión de unidad)
- Sin badge → precio razonable

**Orden dentro de cada categoría:**
- Primero los que tienen flag (⚠), para que el usuario los vea primero
- Luego el resto por nombre alfabético

### Comportamiento de edición inline

Click en "Edit" expande la fila (HTMX swap, mismo patrón que
`stores/_product_row.html`):

```
Espresso  |  [$___.__]  [Unit: Bag 5 lb____]  |  [✓ Confirm]  [✗ Cancel]
           |  Last synced: Excel import (original load)        |
```

**Campos editables:**
- `purchase_price`: numérico, requerido, > 0
- `purchase_unit`: texto libre, requerido

**Al confirmar (POST /admin/price-review/update):**
1. Actualiza `ingredients.purchase_price` y `ingredients.purchase_unit`
2. Inserta o actualiza `price_review_status` con status='reviewed'
3. Retorna la fila actualizada con badge verde "Reviewed" (HTMX swap)
4. Actualiza el contador de progreso en el header (HTMX oob swap)

**Al hacer Skip:**
- POST /admin/price-review/skip
- Marca como 'skipped' en price_review_status
- La fila muestra badge gris "Skipped"
- El progreso NO avanza (skipped ≠ reviewed)

### Endpoints

```
GET  /admin/price-review
     → redirect a /admin/price-review/519 (Edinburgo por defecto)

GET  /admin/price-review/{store_id}
     → overview.html con la tabla completa

GET  /admin/price-review/{store_id}/table
     → partial HTMX: tabla filtrada según query params
     → params: category, status (pending/reviewed/skipped/all)

POST /admin/price-review/update
     body: {ingredient_id, store_id, purchase_price, purchase_unit}
     → actualiza ingredients + price_review_status
     → retorna _ingredient_row.html actualizada + oob del counter

POST /admin/price-review/skip
     body: {ingredient_id, store_id}
     → marca como skipped en price_review_status
     → retorna _ingredient_row.html actualizada
```

### Criterios de aceptación del Paso 2

1. La pantalla muestra exactamente los 145 ingredientes del estado C,
   agrupados por categoría.
2. Los ingredientes con flag aparecen primero dentro de su categoría.
3. Click en "Edit" expande el formulario inline sin cambiar de página.
4. Al confirmar, la fila se actualiza con badge "Reviewed" y el
   contador de progreso avanza.
5. "Skip" marca la fila como gris "Skipped" sin avanzar el contador.
6. El filtro por categoría y por status funciona sin recargar la página.
7. Si se interrumpe y se vuelve a abrir la pantalla, el progreso
   persiste (viene de price_review_status).
8. Los ingredientes sospechosos tienen el badge ⚠ correcto.

---

## Paso 3 — Fix _create_ingredient + backfill estado B

### Problema

`_create_ingredient` en `backend/services/catalog_sync.py` solo inserta
en `ingredients` — no crea `ingredient_supplier_ref` ni `supply_route`
ni `supply_route_price`. Los 7 ingredientes del estado B llegaron con
precio fresco de la API pero sin ruta, así que el motor los trata como
fallback en vez de precio con trazabilidad.

### Fix en _create_ingredient

Después de crear el ingrediente, también crear:

```python
# 1. Resolver o crear el distribuidor
distributor = db.query(Distributor).filter(
    Distributor.name == item['distributor_name']
).first()
if not distributor:
    distributor = Distributor(
        name=item['distributor_name'],
        is_active=True
    )
    db.add(distributor)
    db.flush()

# 2. Crear supply_route
route = SupplyRoute(
    ingredient_id=ingredient.id,
    distributor_id=distributor.id,
    is_direct=False,
    is_active=True,
    metadata={"source": "catalog_sync", "catalog_item_id": item["id"]}
)
db.add(route)
db.flush()

# 3. Crear ingredient_supplier_ref
ref = IngredientSupplierRef(
    ingredient_id=ingredient.id,
    supply_route_id=route.id,
    external_name=item['name'],
    external_code=item.get('sku'),
    purchase_unit=item.get('unit', ''),
    is_active=True
)
db.add(ref)
db.flush()

# 4. Crear supply_route_price
price = SupplyRoutePrice(
    supply_route_id=route.id,
    list_price=item['unit_price'],
    qargo_price=item['unit_price'],
    currency_code='USD',
    price_per_unit=item.get('unit', 'unit'),
    valid_from=date.today(),
    created_by='catalog_sync'
)
db.add(price)
```

### Backfill para los 7 ingredientes B existentes

Crear un script de backfill que aplique la misma lógica a los 7
ingredientes que ya existen sin ruta:

```
IDs: 1 (Milk), 3 (Coconut Milk), 21 (Coconut Syrup),
     23 (Dragon Fruit Syrup), 32 (Strawberry Fruit Puree),
     52 (Water), 68 (Focaccia)
```

El script debe:
1. Para cada uno, leer su `catalog_match_log` más reciente para
   obtener el `catalog_item_id` original y el `distributor_name`
2. Crear `supply_route` + `ingredient_supplier_ref` +
   `supply_route_price` con el precio que ya está en `purchase_price`
3. Crear `supply_route_assignment` para la tienda 519 (Edinburgo)
   con `priority=1`, `valid_from=today`
4. Log de lo que se creó para auditoría

**El backfill se corre UNA SOLA VEZ** — verificar antes de correr que
los 7 IDs no tienen ya una supply_route activa (para no duplicar).

### Criterios de aceptación del Paso 3

1. Después del fix, un nuevo sync que cree ingredientes también
   crea su supply_route + ref + price en la misma transacción.
2. Después del backfill, los 7 ingredientes B tienen supply_route
   activa con supply_route_price vigente.
3. `fn_resolve_supply_route(ingredient_id, 519)` devuelve resultado
   para los 7 ingredientes (antes devolvía NULL para todos).
4. El backfill no crea duplicados si se corre dos veces
   (idempotente — verificar existencia antes de insertar).

---

## Paso 4 — Pricing engine para Edinburgo

### Cuándo correr

Solo después de que:
- El Paso 2 tenga progreso real (mínimo los ingredientes sospechosos
  corregidos y los de mayor volumen revisados)
- El Paso 3 esté completo (los 7 B tienen ruta activa)

### Cómo correr

```python
# Solo para Edinburgo, no para todas las tiendas
pricing_engine.run(store_id=519)
```

O desde la UI: `/stores/519/pricing-overview` → botón
"Calculate prices" si no hay datos calculados.

### Verificación — muestra de 10 productos ancla

Después de correr, revisar estos productos específicamente en
`/stores/519/pricing-overview`:

| Producto | Por qué es ancla |
|---|---|
| Cappuccino Small | Usa Milk (B→A después del backfill) — el más importante |
| Caffe Latte Medium | Usa Milk × 2 — valida el escalado |
| All Butter Croissant | Tiene precio vía ruta (A) — debería ser confiable |
| Cinnamon Roll | Costo conocido de sesiones anteriores (~$0.85) |
| Turkey Bacon Sandwich | Usa Focaccia (B→A) + Turkey Bacon (C) |
| Tiramisu | Tiene precio vía ruta (A) — validar consistencia |
| Cold Brew | Usa Cold Brew ingredient (C, $70.40/gal) — costo alto esperado |
| Matcha Latte | Usa Matcha ($377.50/kg) — costo alto esperado |
| Espresso | Producto simple, 1 ingrediente |
| Pistachio Cheesecake | Tiene precio vía ruta (A) — finished good |

Para cada uno: ¿el costo calculado tiene sentido de negocio?
¿El margen resultante es razonable para la categoría?

---

## Paso 5 — Validación final en producción

### Qué revisar en /stores/519/pricing-overview

1. **0 productos con badge rojo crítico** (margen < 0%) — si hay
   alguno, es una señal de costo sobreestimado o precio de venta
   mal puesto.
2. **Distribución de márgenes por categoría** — ¿las bebidas tienen
   márgenes más altos que la repostería? ¿Los sándwiches tienen
   márgenes razonables?
3. **Los 12 ingredientes estado A** (repostería de reventa) — sus
   costos deben reflejar el precio del empaque de compra dividido
   por las porciones, no el precio unitario del Excel.
4. **Export CSV** — descargar y compartir con Santiago para revisión
   de negocio.

---

## Orden de implementación para Claude Code

```
Paso 1: Migración 0033 + deploy Railway
        → verificar las 4 URLs en producción

Paso 2: Tabla price_review_status + router + templates
        → verificar los 8 criterios de aceptación en local
        → verificar que funciona en producción también

Paso 3: Fix _create_ingredient + script de backfill
        → verificar con fn_resolve_supply_route para los 7 IDs

Paso 4: Pricing engine store_id=519
        → solo después de que el usuario confirme
          que terminó la revisión de precios del Paso 2

Paso 5: Validación manual — el usuario lo hace, no Claude Code
```

**El Paso 4 NO se corre automáticamente** — esperar confirmación
explícita del usuario de que terminó la revisión del Paso 2.

---

## Reglas que NO cambian

- Vigencia temporal en precios: el backfill usa INSERT de nueva
  supply_route_price, no UPDATE de purchase_price como precio de ruta
  (son cosas distintas — purchase_price es el fallback, supply_route_price
  es el precio trazable de ruta).
- Log inmutable: el backfill debe loggear qué creó.
- Transacciones atómicas: si el backfill falla para un ingrediente,
  rollback de ese ingrediente sin afectar los demás.
- Sin inventar precios: el backfill usa el purchase_price existente
  como qargo_price de la ruta — no inventa ningún valor nuevo.
