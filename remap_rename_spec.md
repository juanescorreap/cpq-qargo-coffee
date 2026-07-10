# Re-mapeo y renombre de ingredientes — Especificación

> Documento para Claude Code. Lee esto completo antes de escribir
> cualquier línea de código. Dos funcionalidades en un solo prompt:
> re-mapeo de ingredientes mal asignados y renombre en batch de
> ingredientes nuevos.

---

## Contexto

Después del mapeo masivo de los 236 ingredientes pending review,
el usuario identificó dos problemas:

1. **Más de 15 ingredientes** fueron mapeados al canónico equivocado
   (Caso 1) — la transacción atómica los marcó como MAPPED pero el
   canónico destino es incorrecto.

2. **Más de 15 ingredientes** confirmados como "Keep as new" tienen
   nombres generados automáticamente que no siguen las convenciones
   del proyecto (inglés, Title Case, [Modificador][Base]).

Ambos casos necesitan una UI eficiente para resolverlos en batch,
no uno por uno.

---

## Funcionalidad 1 — Re-mapeo (Caso 1)

### Dónde vive

En `/admin/catalog-sync` → Sección 4, dentro de las cards que
tienen estado **MAPPED** (en el grupo "Already processed").

### Cambio en la card MAPPED

Agregar un botón **"Change mapping"** junto al texto que muestra
el canónico actual:

```
┌─────────────────────────────────────────────────────────────────┐
│ MAPPED → San Pellegrino Pomegranate              [Change mapping]│
│                                                                  │
│ A'SICILIANA Pomegranate Soda Can 11 oz                          │
│ SKU: ASC003 · Local Distributor · $31.20 / case                 │
└─────────────────────────────────────────────────────────────────┘
```

### Comportamiento al hacer click en "Change mapping"

Expande el mismo buscador inline que ya existe para el flujo
original de mapeo (`_ingredient_search_form.html`), pero con
el canónico actual pre-seleccionado y resaltado para contexto:

```
┌─────────────────────────────────────────────────────────────────┐
│ MAPPED                                                           │
│ A'SICILIANA Pomegranate Soda Can 11 oz                          │
│                                                                  │
│ Currently mapped to: San Pellegrino Pomegranate                 │
│ Change to:                                                       │
│ ┌─────────────────────────────────────────────────────────┐     │
│ │ 🔍 Search...                                            │     │
│ └─────────────────────────────────────────────────────────┘     │
│                                                                  │
│                              [Cancel]  [Confirm new mapping]    │
└─────────────────────────────────────────────────────────────────┘
```

### Lógica del backend para re-mapeo

```
POST /admin/catalog-sync/remap-ingredient
body: {
    pending_ingredient_id: int,   ← el duplicado original
    old_canonical_id: int,        ← el canónico actual (incorrecto)
    new_canonical_id: int         ← el nuevo canónico (correcto)
}
```

**Transacción atómica — en orden:**

1. Verificar que `pending_ingredient_id` existe y tiene
   `is_active = false` (fue desactivado por el mapeo original).
2. Verificar que `old_canonical_id` != `new_canonical_id`.
3. Verificar que `new_canonical_id` existe y está activo.
4. **Revertir el mapeo anterior:**
   - Mover ingredient_supplier_refs de `old_canonical_id`
     BACK a `pending_ingredient_id` (solo las que vinieron
     del mapeo original — usar `catalog_match_log` para
     identificarlas por `sync_log_id`)
   - Mover supply_routes de `old_canonical_id` back a
     `pending_ingredient_id`
   - Mover ingredient_availability back
5. **Aplicar nuevo mapeo:**
   - Mover las mismas refs de `pending_ingredient_id` →
     `new_canonical_id`
   - Mover supply_routes → `new_canonical_id`
   - Mover ingredient_availability → `new_canonical_id`
6. Actualizar `catalog_match_log`:
   - `matched_ingredient_id = new_canonical_id`
   - `notes` = append "Remapped from {old_canonical_name}
     to {new_canonical_name} on {date}"
7. El ingrediente duplicado (`pending_ingredient_id`) sigue
   `is_active = false` — no se reactiva.
8. El nombre del duplicado se actualiza:
   `[MAPPED] {original_name}` → `[MAPPED→{new_canonical_name}]
   {original_name}`

**Si cualquier paso falla:** rollback completo, error inline
en la card.

**Respuesta:** card actualizada con badge MAPPED mostrando el
nuevo canónico.

---

## Funcionalidad 2 — Renombre en batch (Caso 2)

### Nueva pantalla: /admin/ingredient-names

Lista todos los ingredientes que fueron confirmados como
"Keep as new" en el catalog sync (los que tienen
`action_taken = 'confirmed_new'` en `catalog_match_log`),
con edición inline de nombre.

### Archivos a crear

```
backend/routers/ingredient_names_ui.py  (nuevo)
templates/admin/ingredient_names/
    overview.html
    _ingredient_row.html
    _edit_form.html
```

### Query para poblar la pantalla

```sql
SELECT DISTINCT
    i.id,
    i.name              AS current_name,
    cml.catalog_name    AS api_name,
    cml.catalog_sku     AS api_sku,
    c.display_name      AS category,
    -- Flag si el nombre no sigue las convenciones
    CASE
        WHEN i.name != initcap(i.name) THEN 'case'
        WHEN i.name ~ '[^a-zA-Z0-9 \-\''\.&]' THEN 'special_chars'
        WHEN i.name = upper(i.name) THEN 'all_caps'
        ELSE 'ok'
    END AS name_flag
FROM catalog_match_log cml
JOIN ingredients i ON i.id = cml.matched_ingredient_id
LEFT JOIN categories c ON c.slug = i.category
WHERE cml.action_taken = 'confirmed_new'
  AND i.is_active = true
ORDER BY name_flag DESC, c.display_name, i.name;
```

### Diseño de la pantalla

**Header:**
```
Ingredient Name Review
N ingredients confirmed as new · review canonical names
```

**Tabla con una fila por ingrediente:**

| Current Name | API Name | Category | Flag | Action |
|---|---|---|---|---|
| AIYA MATCHA - Culinary Grade Matcha | AIYA MATCHA - Culinary Grade Matcha | Matcha | ⚠ CAPS | Edit |
| Cinnamon Bun Brioche Rtb | BRIDOR - Cinnamon Bun Brioche RTB | Bakery | ⚠ Case | Edit |
| Caramel Syrup 750 Ml | MONIN - Caramel Syrup 750 ml | Syrups | ok | Edit |

**Columnas:**
- **Current Name**: `ingredients.name` actual — el que se va a editar
- **API Name**: `catalog_match_log.catalog_name` — nombre original
  de la API, como referencia de dónde vino
- **Category**: categoría del ingrediente
- **Flag**: badge de problema de nomenclatura:
  - ⚠ CAPS → nombre todo en mayúsculas
  - ⚠ Case → no sigue Title Case
  - ⚠ Chars → tiene caracteres especiales no permitidos
  - ok → sin problemas detectados (igual se puede editar)
- **Action**: botón "Edit"

**Orden:** flagged primero (⚠ CAPS, ⚠ Case, ⚠ Chars), luego ok,
dentro de cada grupo por categoría y nombre.

### Comportamiento de edición inline

Click en "Edit" expande la fila:

```
| [Cinnamon Roll Brioche_______] | BRIDOR - Cinnamon Bun Brioche RTB | Bakery | ✓ | ✗ |
```

- Campo de texto pre-llenado con el nombre actual
- Referencia visual del nombre de la API para contexto
- ✓ confirma, ✗ cancela

**Validaciones al confirmar:**
- Campo no vacío
- Máximo 300 caracteres
- No puede ser idéntico a otro `ingredients.name` activo
  (prevenir duplicados canónicos)

**Al confirmar (POST /admin/ingredient-names/update):**
1. Actualiza `ingredients.name`
2. Retorna fila actualizada con el nuevo nombre (HTMX swap)
3. Si hay error de duplicado: mensaje inline
   "An ingredient with this name already exists"

### Endpoints

```
GET  /admin/ingredient-names
     → overview.html con tabla completa

GET  /admin/ingredient-names/table
     → partial HTMX con filtros opcionales
     → params: flag (caps/case/chars/ok/all), category

GET  /admin/ingredient-names/edit/{id}
     → _edit_form.html (fila expandida)

GET  /admin/ingredient-names/row/{id}
     → _ingredient_row.html (collapse de vuelta)

POST /admin/ingredient-names/update
     body: {ingredient_id: int, name: str}
     → valida + actualiza ingredients.name
     → retorna _ingredient_row.html actualizada
     → 400 con mensaje si nombre duplicado
```

---

## Navegación

Agregar ambas pantallas al menú de administración:
- `/admin/catalog-sync` ya existe en el menú
- `/admin/ingredient-names` → agregar como ítem en el menú
  de admin, visible junto a `/admin/catalog-sync` y
  `/admin/price-review`

---

## Pre-checks antes de implementar

1. Confirma el patrón HTMX de la Sección 4 de catalog-sync
   (`_ingredient_card.html`) — el botón "Change mapping" debe
   seguir exactamente el mismo patrón de expand/collapse que
   "Map to existing".

2. Confirma que `catalog_match_log` tiene filas con
   `action_taken = 'confirmed_new'` en producción:
   ```sql
   SELECT COUNT(*) FROM catalog_match_log
   WHERE action_taken = 'confirmed_new';
   ```
   Si el valor es 0, reportarlo antes de continuar — puede
   significar que el valor real es distinto
   (ej. 'confirmed_new' vs 'keep_as_new').

3. Para el re-mapeo: confirma que `catalog_match_log` permite
   identificar qué refs/routes vinieron del mapeo original
   (necesitamos saber cuáles mover y cuáles no tocar).

---

## Criterios de aceptación

### Funcionalidad 1 — Re-mapeo
1. Las cards en estado MAPPED muestran el botón "Change mapping".
2. Click expande el buscador con el canónico actual visible
   como referencia.
3. Seleccionar un nuevo canónico y confirmar ejecuta la
   transacción atómica completa.
4. Si falla cualquier paso, rollback completo y error inline.
5. Después del re-mapeo, la card muestra el nuevo canónico.
6. El `catalog_match_log` refleja el re-mapeo en el campo notes.
7. El ingrediente duplicado sigue inactivo — no se reactiva.

### Funcionalidad 2 — Renombre en batch
1. La pantalla muestra todos los ingredientes con
   `action_taken = 'confirmed_new'`, agrupados con flagged primero.
2. La columna "API Name" muestra el nombre original de la API
   como referencia.
3. Click en "Edit" expande el campo inline pre-llenado con el
   nombre actual.
4. Al confirmar, el nombre se actualiza y la fila refleja el
   nuevo nombre.
5. Si el nombre nuevo ya existe en otro ingrediente activo,
   muestra error inline sin guardar.
6. Sin recarga de página en ningún flujo.
