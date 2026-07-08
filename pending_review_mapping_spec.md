# Flujo de mapeo manual — Pending Review ingredients

> Documento para Claude Code. Lee esto completo antes de escribir
> cualquier línea de código. Esta spec extiende la pantalla
> /admin/catalog-sync (Sección 4) con un flujo de mapeo manual
> para ingredientes creados automáticamente por el catalog sync.

---

## Contexto del problema

El catalog sync crea automáticamente ingredientes cuando un item de
la API no tiene match con ningún ingrediente canónico del CPQ. Después
de el primer sync real, hay 96 ingredientes en estado "Pending review".
De esos 96, aproximadamente la mitad son genuinamente nuevos y la otra
mitad son variantes del mismo ingrediente que ya existe en el CPQ con
un nombre diferente — el fuzzy matcher no los reconoció porque los
nombres son fundamentalmente distintos (no es un problema de umbral,
es un problema de conocimiento de negocio).

El flujo de mapeo manual permite al usuario revisar cada item uno por
uno, buscar el ingrediente canónico correspondiente si existe, y
confirmar el mapeo — o confirmar que es genuinamente nuevo.

---

## Tres acciones posibles por item

Cada ingrediente en "Pending review" tiene exactamente tres acciones:

### 1. Map to existing
El item de la API corresponde a un ingrediente canónico que ya existe
en el CPQ con otro nombre. El usuario busca el canónico y confirma
el mapeo. El ingrediente duplicado se desactiva y todas sus referencias
se reasignan al canónico.

### 2. Keep as new
El item de la API es genuinamente un ingrediente nuevo que no existía
en el CPQ. El nombre generado automáticamente es correcto (o el usuario
ya lo editó). Se confirma como válido y sale de "Pending review".

### 3. Deactivate
El item no es un ingrediente de receta (packaging, limpieza, equipo,
etc. que pasó el filtro de SKIP_SUBCATEGORIES). Se desactiva sin
crear ninguna relación.

---

## Diseño de la card en Sección 4

### Estado base (sin acción activa)

Cada item se muestra como una card horizontal con esta información
y estos controles:

```
┌─────────────────────────────────────────────────────────────────┐
│ PENDING                                                          │
│                                                                  │
│ A'SICILIANA Pomegranate Soda Can 11 oz                          │
│ SKU: ASC003 · Local Distributor · $31.20 / case                 │
│ Synced from store: Fountain Valley · 2026-06-30                 │
│                                                                  │
│ [Map to existing]    [Keep as new]    [Deactivate]              │
└─────────────────────────────────────────────────────────────────┘
```

**Campos a mostrar en la card:**
- `catalog_match_log.catalog_name` — nombre exacto de la API
- `catalog_match_log.catalog_sku` — SKU del distribuidor (si existe)
- `distributor_name` — nombre del distribuidor (de catalog_match_log.notes
  o del ingredient_supplier_ref asociado)
- Precio: `supply_route_prices.qargo_price` + unidad de compra
  de `ingredient_supplier_refs.purchase_unit`
- Tienda de origen y fecha del sync (de catalog_sync_log)

**Badge de estado** en la esquina superior izquierda:
- PENDING → gris
- MAPPED → verde
- CONFIRMED NEW → azul
- DEACTIVATED → rojo/gris oscuro

**Botones:**
- "Map to existing" → primario (color accent del proyecto)
- "Keep as new" → secundario
- "Deactivate" → terciario / destructivo (outlined, no filled)

---

## Flujo 1: Map to existing

### Paso 1 — Click en "Map to existing"

La card se expande inline (HTMX swap) para mostrar el buscador.
NO navega a otra página. Los otros botones se ocultan mientras
el buscador está abierto.

```
┌─────────────────────────────────────────────────────────────────┐
│ PENDING                                                          │
│                                                                  │
│ A'SICILIANA Pomegranate Soda Can 11 oz                          │
│ SKU: ASC003 · Local Distributor · $31.20 / case                 │
│                                                                  │
│ Map to canonical ingredient:                                     │
│ ┌───────────────────────────────────────────────────────────┐   │
│ │ 🔍 Type to search ingredients...                          │   │
│ └───────────────────────────────────────────────────────────┘   │
│                                                                  │
│ (results appear here as user types)                             │
│                                                                  │
│                                    [Cancel]  [Confirm mapping]  │
│                                    (disabled until selection)   │
└─────────────────────────────────────────────────────────────────┘
```

### Paso 2 — Búsqueda en tiempo real

El campo de búsqueda dispara un GET HTMX con debounce de 300ms:

```
GET /admin/catalog-sync/search-ingredients?q={texto}
→ retorna partial HTML con lista de resultados
```

**Comportamiento del buscador:**
- Busca contra `ingredients.name` con ILIKE `%{q}%`
- Solo busca ingredientes con `is_active = true`
- Muestra máximo 8 resultados
- Si el campo está vacío: no muestra resultados (no mostrar todos
  los 185 ingredientes — esperamos que el usuario escriba algo)
- Si no hay resultados: mensaje "No ingredients found. Try a
  different search term."

**Formato de cada resultado en la lista:**
```
San Pellegrino Pomegranate          ← ingredients.name
Beverages · $X.XX / bottle         ← category + precio actual
```

Al hacer click en un resultado: se resalta visualmente (fondo
accent suave) y se habilita el botón "Confirm mapping". Solo
un resultado puede estar seleccionado a la vez.

### Paso 3 — Confirmar el mapeo

Click en "Confirm mapping" → POST HTMX:

```
POST /admin/catalog-sync/map-ingredient
body: {
    pending_ingredient_id: int,   ← id del ingrediente duplicado
    canonical_ingredient_id: int  ← id del canónico seleccionado
}
```

**Lógica del backend (en orden, dentro de una sola transacción):**

```python
def map_to_canonical(pending_id: int, canonical_id: int, db: Session):
    """
    Reasigna todas las referencias del ingrediente duplicado al
    canónico y desactiva el duplicado. Todo en una transacción.
    Si cualquier paso falla, rollback completo.
    """

    # 1. Verificar que pending_id existe y está activo
    pending = db.get(Ingredient, pending_id)
    assert pending is not None and pending.is_active

    # 2. Verificar que canonical_id existe y está activo
    canonical = db.get(Ingredient, canonical_id)
    assert canonical is not None and canonical.is_active

    # 3. Verificar que no son el mismo ingrediente
    assert pending_id != canonical_id

    # 4. Reasignar ingredient_supplier_refs del duplicado al canónico
    db.execute(
        update(IngredientSupplierRef)
        .where(IngredientSupplierRef.ingredient_id == pending_id)
        .values(ingredient_id=canonical_id)
    )

    # 5. Reasignar supply_route_prices via supply_routes del duplicado
    #    Primero reasignar supply_routes al canónico
    db.execute(
        update(SupplyRoute)
        .where(SupplyRoute.ingredient_id == pending_id)
        .values(ingredient_id=canonical_id)
    )

    # 6. Reasignar ingredient_availability del duplicado al canónico
    db.execute(
        update(IngredientAvailability)
        .where(IngredientAvailability.ingredient_id == pending_id)
        .values(ingredient_id=canonical_id)
    )

    # 7. Reasignar ingredient_recipe_unit_conversions si existen
    db.execute(
        update(IngredientRecipeUnitConversion)
        .where(IngredientRecipeUnitConversion.ingredient_id == pending_id)
        .values(ingredient_id=canonical_id)
    )

    # 8. Desactivar el ingrediente duplicado
    pending.is_active = False
    pending.name = f"[MAPPED] {pending.name}"
    # El prefijo [MAPPED] permite identificarlo en auditorías futuras
    # sin borrarlo de la base

    # 9. Actualizar el catalog_match_log para trazabilidad
    db.execute(
        update(CatalogMatchLog)
        .where(CatalogMatchLog.matched_ingredient_id == pending_id)
        .values(
            matched_ingredient_id=canonical_id,
            notes=f"Manually mapped to canonical id={canonical_id} "
                  f"({canonical.name}). Duplicate id={pending_id} deactivated."
        )
    )

    db.commit()
```

**Respuesta del endpoint:**
- Si éxito: retorna la card actualizada como partial HTMX con
  badge "MAPPED" y sin botones de acción (ya está procesado)
- Si error: retorna mensaje de error inline en la card, sin
  recargar la página. Errores posibles:
  - "Canonical ingredient not found or inactive"
  - "Cannot map ingredient to itself"
  - "Database error — no changes were made" (con rollback implícito)

---

## Flujo 2: Keep as new

Click en "Keep as new" → POST HTMX:

```
POST /admin/catalog-sync/confirm-new
body: {pending_ingredient_id: int}
```

**Lógica del backend:**
```python
def confirm_as_new(ingredient_id: int, db: Session):
    # Solo actualiza el estado en catalog_match_log
    db.execute(
        update(CatalogMatchLog)
        .where(CatalogMatchLog.matched_ingredient_id == ingredient_id)
        .values(
            action_taken='confirmed_new',
            notes='Manually confirmed as new ingredient by user'
        )
    )
    db.commit()
    # El ingrediente ya existe activo en la BD — no hay nada más que hacer
```

**Respuesta:** card actualizada con badge "CONFIRMED NEW" y sin
botones de acción.

---

## Flujo 3: Deactivate

Click en "Deactivate" → muestra confirmación inline antes de ejecutar
(prevenir clicks accidentales):

```
┌──────────────────────────────────────────────────────────────┐
│ A'SICILIANA Pomegranate Soda Can 11 oz                       │
│                                                              │
│ ⚠ Deactivate this ingredient?                               │
│ It will be marked inactive and removed from all views.      │
│ This cannot be undone from this screen.                      │
│                                                              │
│                          [Cancel]  [Yes, deactivate]        │
└──────────────────────────────────────────────────────────────┘
```

Click en "Yes, deactivate" → POST HTMX:

```
POST /admin/catalog-sync/deactivate-ingredient
body: {pending_ingredient_id: int}
```

**Lógica del backend:**
```python
def deactivate_pending(ingredient_id: int, db: Session):
    ingredient = db.get(Ingredient, ingredient_id)
    ingredient.is_active = False
    db.execute(
        update(CatalogMatchLog)
        .where(CatalogMatchLog.matched_ingredient_id == ingredient_id)
        .values(action_taken='deactivated_manual')
    )
    db.commit()
```

**Respuesta:** card actualizada con badge "DEACTIVATED" y sin
botones de acción.

---

## Endpoints nuevos

```
GET  /admin/catalog-sync/search-ingredients?q={texto}
     → partial HTML: lista de hasta 8 ingredientes activos
        cuyo nombre contenga el texto (ILIKE %q%)
     → si q vacío: retorna HTML vacío (sin resultados)
     → sin autenticación adicional (ya está en el contexto admin)

POST /admin/catalog-sync/map-ingredient
     body: {pending_ingredient_id: int, canonical_ingredient_id: int}
     → ejecuta map_to_canonical() en una transacción
     → retorna card actualizada (partial HTMX) o error inline

POST /admin/catalog-sync/confirm-new
     body: {pending_ingredient_id: int}
     → retorna card actualizada (partial HTMX)

POST /admin/catalog-sync/deactivate-ingredient
     body: {pending_ingredient_id: int}
     → retorna card actualizada (partial HTMX)

GET  /admin/catalog-sync/review-ingredient/{id}
     → partial HTMX: card con buscador expandido
        (para el estado "Map to existing" abierto)
```

---

## Archivos a modificar / crear

```
backend/routers/catalog_sync_ui.py      ← agregar los 5 endpoints nuevos
backend/services/catalog_sync.py        ← agregar map_to_canonical(),
                                           confirm_as_new(),
                                           deactivate_pending()
templates/admin/catalog_sync/
    _new_ingredients.html               ← MODIFICAR: reemplazar cards
                                           actuales con nuevo diseño
    _ingredient_card.html               ← NUEVO: card individual
                                           (reutilizable por HTMX swap)
    _ingredient_search_results.html     ← NUEVO: partial de resultados
                                           del buscador
    _ingredient_search_form.html        ← NUEVO: partial del buscador
                                           expandido dentro de la card
```

---

## Filtrado de la Sección 4

La Sección 4 debe mostrar las cards agrupadas por estado:

```
Pending review (48)          ← primero, son los que requieren acción
─────────────────────────────
[card] [card] [card] ...

Already processed (48)       ← colapsado por defecto, expandible
─────────────────────────────
Mapped (23) · Confirmed new (18) · Deactivated (7)
```

Esto evita que la sección crezca indefinidamente con items ya
procesados y permite al usuario enfocarse en lo pendiente.

---

## Query para poblar la Sección 4

```sql
SELECT
    i.id                    AS ingredient_id,
    i.name                  AS ingredient_name,
    cml.catalog_name        AS api_name,
    cml.catalog_sku         AS api_sku,
    cml.action_taken        AS status,
    cml.notes               AS notes,
    cml.created_at          AS synced_at,
    isr.purchase_unit       AS purchase_unit,
    srp.qargo_price         AS price,
    srp.currency_code       AS currency,
    s.name                  AS store_name,
    csl.started_at          AS sync_date
FROM catalog_match_log cml
JOIN ingredients i
    ON i.id = cml.matched_ingredient_id
LEFT JOIN ingredient_supplier_refs isr
    ON isr.ingredient_id = i.id
LEFT JOIN supply_routes sr
    ON sr.ingredient_id = i.id AND sr.is_active = true
LEFT JOIN supply_route_prices srp
    ON srp.supply_route_id = sr.id AND srp.valid_until IS NULL
JOIN catalog_sync_log csl
    ON csl.id = cml.sync_log_id
JOIN stores s
    ON s.id = csl.store_id
WHERE cml.match_type = 'new'
ORDER BY
    CASE cml.action_taken
        WHEN 'ingredient_created' THEN 0   -- pending primero
        ELSE 1
    END,
    cml.created_at DESC
```

---

## Reglas que NO cambian

- **Transacción atómica**: map_to_canonical() es todo o nada.
  Si cualquier paso falla, rollback completo. El usuario ve un
  error claro, no un estado parcial en la BD.
- **Nunca borrar**: los ingredientes duplicados se desactivan
  con prefijo [MAPPED], no se eliminan. Siempre hay trazabilidad.
- **Log inmutable**: catalog_match_log solo se actualiza el campo
  action_taken y notes — nunca se borran filas.
- **Sin recarga de página**: todos los flujos son HTMX inline.
  El usuario permanece en /admin/catalog-sync durante todo el proceso.

---

## Criterios de aceptación

1. Cada card en Sección 4 muestra nombre API, SKU, distribuidor,
   precio, tienda de origen y los tres botones de acción.
2. Click en "Map to existing" expande el buscador inline sin
   cambiar de página.
3. Escribir en el buscador muestra resultados en tiempo real
   (debounce 300ms) filtrando solo ingredientes activos.
4. Seleccionar un resultado resalta esa fila y habilita
   "Confirm mapping".
5. "Confirm mapping" ejecuta map_to_canonical() en una transacción:
   reasigna refs, desactiva duplicado, actualiza log.
6. Si el mapeo falla, el usuario ve un mensaje de error inline
   y la BD no queda en estado parcial.
7. "Keep as new" actualiza action_taken='confirmed_new' y muestra
   badge azul "CONFIRMED NEW".
8. "Deactivate" muestra confirmación antes de ejecutar, luego
   desactiva y muestra badge rojo "DEACTIVATED".
9. Items procesados se agrupan en "Already processed" colapsado,
   separados de los "Pending review".
10. El ingrediente duplicado desactivado tiene prefijo [MAPPED]
    en su nombre para trazabilidad en auditorías.
11. Después de un mapeo exitoso, si hay un pricing snapshot que
    usaba el ingrediente duplicado, ese snapshot sigue siendo
    válido (la reasignación de supply_routes lo cubre).

---

## Pre-checks antes de implementar

1. Confirma que `catalog_match_log` tiene el campo `action_taken`
   con valor `'ingredient_created'` para los 96 items pendientes
   (SELECT COUNT(*) FROM catalog_match_log WHERE action_taken =
   'ingredient_created'). Si el valor es distinto, ajusta la query
   de la Sección 4.
2. Confirma que los 96 ingredientes pendientes tienen filas en
   `ingredient_supplier_refs` y `supply_routes` antes de implementar
   la lógica de reasignación — si alguno no las tiene, la
   transacción debe manejarlo sin fallar (las reasignaciones que no
   tienen filas simplemente no hacen nada, no lanzan error).
3. Confirma el patrón de HTMX swap que usa el resto de la UI para
   updates inline de cards (ej. cómo lo hace el toggle de
   is_available en stores) — úsalo como referencia, no inventes
   un patrón nuevo.
