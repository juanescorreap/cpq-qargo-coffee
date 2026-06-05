# Diseño Front-End — Módulo Proveedores y Rutas de Abastecimiento

> Arquitectura de componentes, estado y flujos para expandir el front-end al
> dominio de Sourcing. Diseñado sobre el stack y el módulo supply-chain reales,
> reutilizando el backend de las Fases 1-5 del motor de costos.

## Aclaración de stack (no hay React/TanStack)

El front es **MPA server-rendered: FastAPI + Jinja2 + HTMX 2 + Alpine 3 + Tailwind
(compilado)**. No existe React/TanStack/Redux. Se traduce cada concepto:

| Concepto React | Equivalente real |
|---|---|
| TanStack Query (queries) | `hx-get` → endpoint que devuelve parcial Jinja |
| Mutations | `hx-post`/`hx-put` → devuelve el parcial actualizado |
| Estado global (Redux/Context) | **Server state** (sin store cliente); Alpine = estado efímero local |
| Optimistic updates | Alpine aplica el valor ya + rollback en `htmx:responseError` |
| Invalidación de caché | header `HX-Trigger` + cola `calc_jobs` (recálculo server-side) |
| Error boundary | parcial re-renderizado con error inline (patrón existente `price_error`) |

**Ya existe** módulo supply-chain: `backend/templates/supply_chain/{routes,assignments,
regions,manufacturers,distributors}/` + `routers/supply_chain_ui.py` +
`supply_route_prices.py` / `supply_route_assignments.py`. Patrones en uso:
parciales `_content.html`/`_prices.html`, filtros `hx-trigger="change delay"`,
error inline en el parcial.

**Gaps que este diseño cierra:**
1. UI de `supplier_unit_conversions` (Caja 10kg → gramos) — no existe.
2. Edición masiva tipo matriz — hoy solo precio por ruta individual.
3. Metadata de proveedor (criticidad / `lead_time_days` / `tags`) — en `metadata JSONB`, sin surface.
4. **Enrutar precios por `fn_ingest_route_price`** — hoy `create_route_price_htmx`
   hace close+insert manual, **saltándose el advisory lock y el outbox** (fix clave).

---

## 1. Arquitectura de pantallas y flujos (UX)

### 1.1 Panel de Proveedores — `/supply-chain/suppliers`
Unifica `manufacturers` + `distributors` con la `metadata JSONB`.

```
┌─ Suppliers ───────────────────────────────────────────────┐
│ [🔎 buscar]  Criticidad:[Alta▾] Lead≤:[7d] Tags:[lácteos✕] │ ← filtros server-side
├───────────────────────────────────────────────────────────┤
│ Nombre        Tipo     Crit.  Lead  Tags        Rutas activas│
│ Alpina        Fabric.  🔴Alta  3d   lácteos     12          │
│ Distrib.Norte Distrib. 🟡Med   7d   bebidas...   8          │
└───────────────────────────────────────────────────────────┘
```
- **Filtrado server-side** (densidad alta). Patrón: `hx-trigger="change delay:300ms"`
  + `hx-include` (ver `products/list.html:32`) → `hx-get /suppliers/_content?...`
  devuelve solo la tabla. Sin recargar el shell.
- Fila → `hx-get` al detalle (rutas que surte, historial).

### 1.2 Matriz de Rutas y Precios (Hot Path) — `/supply-chain/matrix`
Vista densa: **ingrediente × (región/tienda) → ruta activa + precio Qargo + vigencia**.

```
Ingrediente: [Leche entera ▾]   Región:[Todas▾]   (acota filas → perf)
┌──────────┬───────────────┬──────────┬───────────┬─────────────┐
│ Scope    │ Ruta(fab/dist)│ Qargo    │ valid_from│ valid_until │
│ BOG (reg)│ Alpina/Norte  │ 4.500 COP│ 2026-01-01│ —  [editar] │
│ Tienda 3 │ Alpina/dir.   │ 4.200 COP│ 2026-03-01│ —  [editar] │ ← override
└──────────┴───────────────┴──────────┴───────────┴─────────────┘
```
- **Filtro obligatorio** (ingrediente o región) antes de renderizar filas → acota el
  set, evita miles de celdas. Sin filtro → estado vacío con CTA.
- **Edición inline por celda:** click → mini-form (`hx-get .../prices/edit`) → guardar
  `hx-post` a **`fn_ingest_route_price`** (lock + outbox). La fila se re-renderiza con
  badge "recalculando…" hasta que el worker termine.
- **Vigencia:** `valid_until = —` = vigente. El `EXCLUDE no_overlap_srp` impide ventanas
  solapadas; el form solo pide `valid_from` (default hoy), el cierre lo hace close+insert.

### 1.3 Panel de Conversiones de Unidad — `/supply-chain/routes/{id}` tab "Unidades"
Sobre `supplier_unit_conversions`. Hoy solo hay `_refs.html` (nombre/código externo).

```
Ref proveedor: "Leche Caja 10kg" (purchase_unit: caja)
┌─ Conversión a unidad de receta ──────────────────────────┐
│ 1 [caja]  =  [10000] [gramo ▾]     [✓ válido]  [Guardar] │
│ 1 [caja]  =  [10]    [kilogramo▾]  [✓ válido]  [Guardar] │
│ + añadir conversión                                       │
└──────────────────────────────────────────────────────────┘
```
- Una fila por `(ingredient_ref, recipe_unit)` (UNIQUE en BD). Validación en tiempo
  real `purchase_qty>0 ∧ recipe_qty>0` (espeja `CHECK suc_quantities_positive`).
- Es la conversión que el motor consume (`fn_resolve_ingredient_sourcing`, Fase 2);
  editar aquí dispara recálculo vía outbox.

---

## 2. Carga de datos y estado

### 2.1 Queries/mutations (HTMX)
- **Query** = endpoint `_content`/`_prices`/`_conversions` → parcial (patrón `_routes_context`, `_render_prices`).
- **Mutation** = `hx-post` que devuelve el **mismo parcial actualizado** → "refetch" implícito, sin store cliente.
- **Caché de navegación:** `hx-history` + `hx-boost` en listas; matriz con `HX-Push-Url`
  y filtros en querystring → back/forward reconstruye desde el server (la "caché" es la URL).

### 2.2 Optimistic updates (Alpine + HTMX)
```html
<!-- celda de precio editable, optimista -->
<td x-data="{ pending:false, prev:'{{ price.qargo_price }}' }"
    :class="pending && 'opacity-50 animate-pulse'">
  <form hx-post="/supply-chain/routes/{{ route_id }}/prices/htmx"
        hx-target="closest tr" hx-swap="outerHTML"
        @htmx:before-request="pending=true; prev=$el.qargo.value"
        @htmx:response-error="pending=false; $el.qargo.value=prev"  <!-- rollback -->
        @htmx:after-on-load="pending=false">
    <input name="qargo_price" :value="prev" class="w-24" />
  </form>
</td>
```
UI instantánea (valor + pulse) y **se revierte sola** si el server rechaza (lock/EXCLUDE/validación).

### 2.3 Invalidación de caché — aprovecha el backend
Cambiar un precio dispara recálculo (Fase 5): `fn_ingest_route_price` → trigger outbox
→ `calc_jobs(route_change)` → worker recalcula `recipe_cost_snapshots`/`product_pricing`.

**Front:** el endpoint responde con `HX-Trigger` para marcar obsoletas las dependientes:
```python
return HTMLResponse(html, headers={"HX-Trigger": "prices-changed"})
```
Escuchan `hx-trigger="prices-changed from:body"` y se refrescan:

| Pantalla | Qué queda stale |
|---|---|
| Calculadora de costos (`_result`) | costo por porción del producto afectado |
| Pricing manager | precio sugerido/markup |
| Reports dashboard | márgenes, KPIs de costo |
| Route detail (`_prices`) | badge "precio vigente" |
| Matriz | celda + "recalculando…" hasta que el worker confirme |

> Recálculo **asíncrono** (worker drena `calc_jobs`). La UI muestra "recalculando…"
> y al refrescar ve el snapshot nuevo. No bloquea el request por un BOM grande.

---

## 3. Componentes críticos y errores

### 3.1 Edición en lote / ingesta masiva — race conditions
El backend ya mitiga: `fn_ingest_route_price` toma `pg_advisory_xact_lock('srp:'||route_id)`
(serializa por ruta) y `EXCLUDE no_overlap_srp`/`no_overlap_sra` bloquean solapamientos.
El front solo **degrada con gracia**:

- **Lock concurrente:** el advisory lock **espera** (no falla) → el request tarda más;
  mostrar spinner (`hx-indicator`). Nada que manejar.
- **EXCLUDE / validación:** el server captura y devuelve **el parcial con error inline**
  (patrón `price_error`/`ref_error`), no un 500 crudo. Para lote, **parcial de resultados
  por fila** (✓/✗ + motivo), como ya hace `scraping_ui`:
```
Ingesta (12 filas):
 ✓ Ruta 5  4.500 COP
 ✗ Ruta 8  — solapa con precio vigente (2026-02-01) — corrige valid_from
 ✓ Ruta 9  3.200 COP
```
- **Estrategia de lote:** `ingest_route_prices` (Fase 5) es todo-o-nada (rollback en
  cualquier fila). Para "guarda lo válido", envolver cada fila en su `begin_nested`
  (savepoint) y reportar por fila (best-effort) — recomendado para edición masiva.
- Red de seguridad global: handler `htmx:responseError` (FRONTEND_AUDIT #3) → toast.

### 3.2 Skeletons para matrices densas
```html
<div hx-get="/supply-chain/matrix/_rows?ingredient_id={{ ing }}"
     hx-trigger="load" hx-swap="innerHTML">
  {% for _ in range(8) %}
  <div class="grid grid-cols-5 gap-3 py-3 border-b">
    {% for _ in range(5) %}<div class="h-4 bg-stone-200 rounded animate-pulse"></div>{% endfor %}
  </div>
  {% endfor %}
</div>
```
`animate-pulse` ya disponible (Tailwind compilado). El skeleton refleja la geometría
real (5 columnas) → cero CLS.

---

## 4. Código de referencia (stack real)

### 4.1 Servicio para guardar un precio — manejo estricto de zona horaria
`valid_from` es DATE de calendario en zona del negocio (`America/Bogota`). Usar
`date.today()` (UTC en prod) cerca de medianoche cierra/abre la ventana el día
equivocado. Fix: "hoy" en la TZ del negocio.

```python
# backend/services/supplier_pricing.py
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from sqlalchemy import text

BUSINESS_TZ = ZoneInfo("America/Bogota")

def business_today() -> date:
    """Calendar 'today' in business TZ — never the UTC date."""
    return datetime.now(BUSINESS_TZ).date()

def save_route_price(db, *, route_id: int, list_price: str, qargo_price: str,
                     currency: str, price_unit_id: int | None, price_per_unit: str,
                     created_by: str, source: str | None = None,
                     valid_from: date | None = None) -> int:
    """Save a new price via fn_ingest_route_price (advisory lock + outbox).
    Returns the new price id. Raises ValueError on invalid input."""
    try:
        lp, qp = Decimal(list_price), Decimal(qargo_price)
    except InvalidOperation:
        raise ValueError("Precio inválido")
    if lp <= 0 or qp <= 0:
        raise ValueError("Los precios deben ser mayores que cero")
    if qp > lp:
        raise ValueError("El precio negociado no puede exceder el de lista")
    currency = currency.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("Moneda inválida (3 letras, ej. COP)")

    vf = valid_from or business_today()
    new_id = db.execute(text("""
        SELECT fn_ingest_route_price(
            :route, :lp, :qp, :ccy, :unit, :per, :source, :by, :vf)
    """), {"route": route_id, "lp": lp, "qp": qp, "ccy": currency,
           "unit": price_unit_id, "per": price_per_unit, "source": source,
           "by": created_by, "vf": vf}).scalar()
    db.commit()
    return new_id
```
> El front envía `valid_from` como **string `YYYY-MM-DD`** (no `Date()`/ISO con `Z`),
> evitando el shift UTC. Vacío → server usa `business_today()`. El endpoint responde
> con `HX-Trigger: prices-changed`.

### 4.2 Fila de mapeo de unidad con validación en tiempo real (Alpine)
Espeja el `CHECK suc_quantities_positive` (>0); el server revalida (defensa en profundidad).

```html
<!-- supply_chain/routes/_conversion_row.html -->
<tr x-data="conversionRow({{ ref.id }}, '{{ ref.purchase_unit }}')">
  <td class="text-sm text-stone-600">1 {{ ref.purchase_unit }} =</td>
  <td>
    <input type="number" step="any" x-model.number="recipeQty"
           class="w-28 rounded border px-2 py-1"
           :class="!valid && 'border-red-500 bg-red-50'" />
  </td>
  <td>
    <select x-model.number="recipeUnitId" class="rounded border px-2 py-1">
      {% for u in recipe_units %}<option value="{{ u.id }}">{{ u.name }}</option>{% endfor %}
    </select>
  </td>
  <td>
    <span x-show="valid"  class="text-emerald-600 text-xs">✓ válido</span>
    <span x-show="!valid" x-cloak class="text-red-600 text-xs" x-text="error"></span>
  </td>
  <td>
    <button :disabled="!valid"
            class="px-3 py-1 rounded bg-espresso text-cream text-sm
                   disabled:opacity-40 disabled:cursor-not-allowed"
            hx-post="/supply-chain/routes/{{ route_id }}/conversions/htmx"
            hx-include="closest tr" hx-target="#conversions-panel" hx-swap="innerHTML"
            :hx-vals="JSON.stringify({ ingredient_ref_id: refId, recipe_unit_id: recipeUnitId, recipe_qty: recipeQty })">
      Guardar
    </button>
  </td>
</tr>

<script>
function conversionRow(refId, purchaseUnit) {
  return {
    refId, purchaseUnit,
    recipeQty: null, recipeUnitId: null, error: "",
    get valid() {
      if (this.recipeQty === null || this.recipeQty === "") { this.error = "Requerido"; return false; }
      if (!(this.recipeQty > 0)) { this.error = "Debe ser > 0"; return false; }   // no 0/negativos
      if (!this.recipeUnitId) { this.error = "Elige unidad"; return false; }
      this.error = ""; return true;
    },
  };
}
</script>
```
```python
# handler: revalida server-side (nunca confíes solo en el cliente)
@router.post("/routes/{route_id}/conversions/htmx")
def create_conversion_htmx(route_id: int, request: Request,
        ingredient_ref_id: int = Form(...), recipe_unit_id: int = Form(...),
        recipe_qty: str = Form(...), db: Session = Depends(get_db)):
    try:
        rq = Decimal(recipe_qty)
        if rq <= 0:
            raise ValueError("La cantidad de receta debe ser mayor que cero")
        with db.begin_nested():
            db.add(SupplierUnitConversion(
                ingredient_ref_id=ingredient_ref_id, recipe_unit_id=recipe_unit_id,
                purchase_qty=Decimal("1"), recipe_qty=rq))
        db.commit()
        return _render_conversions(route_id, request, db)
    except (ValueError, InvalidOperation) as exc:
        return _render_conversions(route_id, request, db, error=str(exc))
    except IntegrityError:        # UNIQUE(ref,recipe_unit) o CHECK
        db.rollback()
        return _render_conversions(route_id, request, db,
            error="Ya existe una conversión para esa unidad")
```

---

## Resumen del enfoque

- **Sobre lo existente** (HTMX partials + inline-error + Alpine), no un rewrite a React.
- **Reutiliza Fases 1-5**: `fn_ingest_route_price` (lock+outbox), `calc_jobs`
  (invalidación asíncrona), `EXCLUDE` (anti-solapamiento), `fn_resolve_ingredient_sourcing`.
- **Cierra gaps**: UI conversiones, matriz densa con filtro obligatorio, metadata de
  proveedor, y enrutar precios por `fn_ingest_route_price` (hoy se salta lock+outbox).
- **Performance**: filtrado/paginación server-side, skeletons sin CLS, recálculo fuera del request.

### Orden de implementación sugerido
1. Panel de conversiones de unidad (gap claro, bajo riesgo). 4.2 listo para usar.
2. Migrar `create_route_price_htmx` → `save_route_price`/`fn_ingest_route_price` (gana lock+outbox).
3. `HX-Trigger: prices-changed` + listeners en pantallas dependientes.
4. Matriz de rutas con filtro obligatorio + skeletons.
5. Panel de proveedores unificado con metadata.
6. Ingesta masiva por filas (best-effort + parcial de resultados).
