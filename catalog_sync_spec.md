# Integración API Catalog → CPQ — Especificación de implementación

> Documento para Claude Code. Lee esto completo antes de escribir
> cualquier línea de código.

---

## Contexto

Existe una API interna de Qargo en:
`https://72.60.171.43.sslip.io/api/catalog/?store_id={n}`

Devuelve un array JSON de items de catálogo por tienda. El objetivo es
sincronizar precios de ingredientes desde esta API hacia el CPQ,
actualizando los existentes y creando ingredientes nuevos cuando la
API trae algo que el CPQ no conoce todavía.

La URL base va en variable de entorno `CATALOG_API_BASE_URL` — nunca
hardcodeada en el código.

---

## Estructura de un item de la API (campos relevantes)

```json
{
    "id": 124,
    "sku": "ASC003",
    "name": "A'SICILIANA Pomegranate Soda Can 11 oz",
    "category": "BEVERAGE",
    "category_name": "Beverages",
    "subcategory_name": "Soda",
    "unit": "case",
    "pack_size": "6/4ct / 11 oz",
    "unit_price": 31.2,
    "distributor_name": "Local Distributor",
    "distributor_sku": null,
    "is_out_of_stock": false,
    "is_in_stock": true,
    "is_seasonal": false,
    "variants": [],
    "has_variants": false
}
```

Campos ignorados (no mapean a ninguna entidad del CPQ):
`material`, `thickness`, `size`, `storage_temp`, `allergens`,
`shelf_life_days`, `is_halal`, `display_image_url`, `order_units`.

---

## Nuevas tablas de schema requeridas

### store_catalog_mapping

```sql
CREATE TABLE public.store_catalog_mapping (
    id                  SERIAL PRIMARY KEY,
    store_id            INTEGER NOT NULL REFERENCES stores(id),
    catalog_store_id    INTEGER NOT NULL,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (store_id),
    UNIQUE (catalog_store_id)
);

COMMENT ON TABLE public.store_catalog_mapping IS
    'Mapeo entre el store_id del CPQ y el store_id de la API de catálogo
     externa. Se configura manualmente desde /admin/catalog-sync.
     Una tienda sin mapeo no puede sincronizarse.';
```

### catalog_sync_log

```sql
CREATE TABLE public.catalog_sync_log (
    id                  SERIAL PRIMARY KEY,
    store_id            INTEGER REFERENCES stores(id),
    catalog_store_id    INTEGER NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,
    triggered_by        VARCHAR(50) NOT NULL,
    items_fetched       INTEGER,
    items_matched       INTEGER,
    items_created       INTEGER,
    items_updated       INTEGER,
    items_skipped       INTEGER,
    items_error         INTEGER,
    status              VARCHAR(20) NOT NULL DEFAULT 'running',
    error_detail        TEXT,
    metadata            JSONB
);
```

### catalog_match_log

```sql
CREATE TABLE public.catalog_match_log (
    id                    SERIAL PRIMARY KEY,
    sync_log_id           INTEGER NOT NULL REFERENCES catalog_sync_log(id),
    catalog_item_id       INTEGER NOT NULL,
    catalog_sku           VARCHAR(100),
    catalog_name          VARCHAR(300) NOT NULL,
    match_type            VARCHAR(20),
    matched_ingredient_id INTEGER REFERENCES ingredients(id),
    fuzzy_score           NUMERIC,
    action_taken          VARCHAR(20),
    old_price             NUMERIC,
    new_price             NUMERIC,
    currency_code         CHAR(3),
    notes                 TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## UI de administración — /admin/catalog-sync

### Sección 1 — Configuración de mapeo de tiendas

Primera sección de la página. Sin este paso completado, ninguna sync
puede correr.

Layout: tabla con una fila por cada una de las 17 tiendas del CPQ:

| Tienda | Código | Catalog Store ID | Estado | Acción |
|---|---|---|---|---|
| Fountain Valley | 2-FV-CA | 13 | Configured | Edit |
| Long Beach | 2-LB-CA | — | Not configured | Set ID |

- Tienda y Código: stores.name y stores.code
- Catalog Store ID: editable inline
- Estado: badge verde (Configured) o amarillo (Not configured)
- Acción: "Edit" si tiene mapeo, "Set ID" si no

Al hacer click en "Set ID" / "Edit": input numérico inline via HTMX.
El sistema valida que el catalog_store_id no esté asignado a otra
tienda. Si hay duplicado: mensaje inline "This catalog ID is already
assigned to [nombre de tienda]".

Tiendas sin mapeo: botón "Sync now" deshabilitado con tooltip
"Configure catalog ID first".

### Sección 2 — Estado y control de sincronización

Tabla por tienda configurada: último sync, items, matches, nuevos,
estado, botón "Sync now".

Botón global "Sync all stores" en la parte superior.

Mientras una sync corre: botón muestra "Running..." y se deshabilita.
Al terminar: fila se actualiza via HTMX con los nuevos conteos.

### Sección 3 — Log de sincronizaciones recientes

Tabla paginada de catalog_sync_log, últimas 20 syncs.
Cada fila expandible para ver detalle de catalog_match_log.

### Sección 4 — Panel de revisión de ingredientes nuevos

Ingredientes creados automáticamente (sin match en el CPQ):
- Nombre canónico generado, nombre original de la API, distribuidor
- Estado: "Pending review"
- Acción: "Assign to recipe" o "Deactivate"

---

## Endpoints

```
GET  /admin/catalog-sync
     → overview.html completo

POST /admin/catalog-sync/mapping
     body: {store_id: int, catalog_store_id: int}
     → 400 si catalog_store_id duplicado

DELETE /admin/catalog-sync/mapping/{store_id}
     → elimina mapeo

POST /admin/catalog-sync/run?store_id={n}
     → 400 si tienda sin mapeo

POST /admin/catalog-sync/run-all
     → sync para todas las tiendas configuradas

GET  /admin/catalog-sync/log/{sync_id}
     → detalle de catalog_match_log (partial HTMX)
```

---

## Arquitectura del servicio

### Archivo a crear

```
backend/services/catalog_sync.py
backend/routers/catalog_sync_ui.py
templates/admin/catalog_sync/
    overview.html
    _mapping_table.html
    _sync_status.html
    _sync_log.html
    _new_ingredients.html
```

### Clase principal

```python
class CatalogSyncService:

    async def sync_store(self, store_id: int, triggered_by: str) -> CatalogSyncLog:

    async def sync_all_stores(self, triggered_by: str) -> list[CatalogSyncLog]:

    def _fetch_catalog(self, catalog_store_id: int) -> list[dict]:
        """GET con timeout=30s y 2 reintentos."""

    def _match_item(self, item, existing_refs, existing_names) -> MatchResult:

    def _parse_pack_size(self, pack_size: str) -> dict | None:

    def _normalize_name(self, raw_name: str) -> str:

    def _update_price(self, ingredient_id, new_price, currency, source) -> bool:
        """Cierre + INSERT. Nunca UPDATE de fila existente."""

    def _create_ingredient(self, item: dict) -> int:
        """Sin recipe_ingredients. Necesita revisión manual."""
```

---

## Estrategia de matching (jerarquía)

### 1. SKU exacto — alta confianza, sin revisión

```python
if item['sku'] and item['sku'] in existing_refs:
    return MatchResult(type='sku_exact', score=1.0,
                       ingredient_id=existing_refs[item['sku']])
```

### 2. Fuzzy name — score >= 90, automático con log

```python
from rapidfuzz import fuzz, process
best = process.extractOne(
    normalize_for_matching(item['name']),
    existing_names.keys(),
    scorer=fuzz.WRatio,
    score_cutoff=90
)
if best:
    return MatchResult(type='fuzzy_name', score=best[1],
                       ingredient_id=existing_names[best[0]])
```

### 3. Sin match — crear o skip

```python
SKIP_SUBCATEGORIES = {
    'Cleaning', 'Cleaning Supplies', 'Packaging',
    'Supplies', 'Equipment', 'Paper Goods'
}
if item.get('subcategory_name') in SKIP_SUBCATEGORIES:
    return MatchResult(type='skipped', reason='non-ingredient subcategory')
if item['unit_price'] and item['unit_price'] > 0:
    return MatchResult(type='new')
return MatchResult(type='skipped', reason='no price')
```

---

## Parseo de pack_size

Ejemplos reales:
```
"6/4ct / 11 oz"  → 264 oz por case
"12 / 1 lb"      → 12 lb por case
"1 / 5 kg"       → 5 kg por case
```

Si el parseo falla: actualizar precio, NO crear supplier_unit_conversions,
registrar `notes: "pack_size not parsed"` en catalog_match_log.
Nunca inventar el valor.

---

## Mapeo de campos API → CPQ

| Campo API | Destino CPQ | Notas |
|---|---|---|
| unit_price | ingredients.purchase_price | Precio base |
| unit_price | supply_route_prices.qargo_price | Por ruta |
| unit | ingredients.purchase_unit | "case", "each", "lb" |
| sku | ingredient_supplier_refs.external_code | SKU externo |
| pack_size | supplier_unit_conversions | Solo si parseable |
| distributor_name | distributors.name | Lookup por nombre |
| is_out_of_stock=true | ingredient_availability | status='shortage' |
| is_seasonal=true | ingredient_availability | status='seasonal' |

### Actualización de disponibilidad

```
is_out_of_stock=true  → INSERT ingredient_availability (status='shortage')
                         si no hay fila activa para ese ingrediente
is_out_of_stock=false → cerrar fila activa (UPDATE valid_until=today)
Mismo patrón para is_seasonal
```

---

## Scheduler

```python
scheduler.add_job(
    CatalogSyncService().sync_all_stores,
    'cron',
    day_of_week='mon',
    hour=6,
    args=['scheduler'],
    id='catalog_sync_weekly'
)
```

Frecuencia configurable via `CATALOG_SYNC_SCHEDULE` (env var).
Default: lunes 6am.

---

## Reglas que NO cambian

- Vigencia temporal en precios: cierre + INSERT, nunca UPDATE directo.
- Nombres en inglés, Title Case: mismas reglas de nomenclatura.
- Log inmutable: catalog_sync_log y catalog_match_log son append-only.
- Sin receta automática: ingredientes nuevos sin recipe_ingredients.
- Sin conversiones inventadas: si pack_size no parsea, se omite.

---

## Pre-checks antes de implementar

Antes de escribir código, confirma y reporta:

1. ¿APScheduler está en requirements.txt? Si no, agrégalo.
2. ¿Qué cliente HTTP existe (httpx, requests)? Úsalo, no instales nuevo.
3. ¿rapidfuzz está en requirements.txt? Ya se usó en el ETL.
4. Confirma autenticación de la API:
   curl -I "https://72.60.171.43.sslip.io/api/catalog/?store_id=13"
   Si responde 401/403, necesito el header de auth antes de continuar.
5. URL base va en .env como CATALOG_API_BASE_URL — nunca hardcodeada.

---

## Criterios de aceptación

1. /admin/catalog-sync muestra las 17 tiendas con estado de mapeo.
2. Puedo escribir catalog_store_id para cualquier tienda inline, sin recargar.
3. Duplicado rechazado con mensaje de qué tienda ya lo tiene.
4. "Sync now" deshabilitado para tiendas sin mapeo.
5. Sync corre y el resultado aparece en pantalla al terminar.
6. SKU exacto en ingredient_supplier_refs → match directo.
7. Fuzzy score >= 90 → match automático con score en log.
8. Sin match → ingrediente nuevo sin receta, visible en panel de revisión.
9. Subcategorías no-ingrediente → skipped.
10. Precio via cierre + INSERT (nunca UPDATE directo).
11. is_out_of_stock=true → fila en ingredient_availability.
12. Scheduler corre según CATALOG_SYNC_SCHEDULE.
13. catalog_match_log permite auditar cada item de cada sync.
