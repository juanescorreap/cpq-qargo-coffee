# Store Pricing Overview — Especificación de implementación

> Documento para Claude Code. Lee esto completo antes de escribir
> cualquier línea de código. Contiene todas las decisiones de diseño,
> comportamiento, y restricciones ya confirmadas con el negocio.

---

## Contexto del proyecto

Esta es una nueva vista del CPQ de Qargo Coffee — una aplicación
FastAPI + Jinja2 + HTMX + Tailwind CSS con server-side rendering y
partials HTMX. Sin React. El patrón de la UI ya existe: las pantallas
de Ingredients, Products, Stores, Supply Chain, y el Pricing Manager
son la referencia de cómo se construye todo aquí.

La nueva vista se llama **Store Pricing Overview** y permite ver todos
los costos y precios de los productos de una tienda específica en una
sola pantalla, con alertas visuales de margen, búsqueda, filtros, y
exportación a CSV.

---

## Ubicación en la navegación — DOS puntos de entrada

### Punto de entrada 1: menú principal

Agregar `Pricing Overview` como ítem de navegación al mismo nivel que
Stores, Ingredients, Products. Ruta: `/pricing/overview`.

Cuando se accede desde aquí, el selector de tienda empieza sin
preselección — el usuario elige la tienda antes de ver la tabla.

### Punto de entrada 2: tab dentro del detalle de tienda

En `/stores/{id}`, agregar un tab `Pricing Overview` junto a los tabs
existentes (Active Routes, etc.). Ruta: `/stores/{id}/pricing-overview`
o como partial HTMX dentro del tab.

Cuando se accede desde aquí, la tienda ya está preseleccionada (la
del detalle actual). El selector de tienda puede ocultarse o mostrarse
en modo readonly para que el usuario sepa qué tienda está viendo.

Ambos puntos de entrada cargan la misma vista — no son dos páginas
distintas, solo dos formas de llegar a la misma pantalla.

---

## Estructura de la pantalla — de arriba a abajo

### 1. Selector de tienda

Dropdown con las 17 tiendas activas. Al cambiar la tienda seleccionada,
la tabla y el panel de resumen se actualizan via HTMX sin recargar la
página completa.

### 2. Panel de resumen (4 métricas)

Mostrar en cards o badges en una sola fila horizontal:

```
[ N productos ]  [ Markup prom: X% ]  [ GM prom: Y% ]  [ ⚠ N bajo umbral ]
```

- **N productos**: conteo total de combinaciones producto+talla activas
  para la tienda seleccionada (no productos únicos — si Cappuccino tiene
  3 tallas, cuenta como 3).
- **Markup prom**: promedio de markup de todos los productos de la
  tienda. Markup = (precio / costo - 1) × 100.
- **GM prom**: promedio de gross margin. GM = (precio - costo) / precio × 100.
- **⚠ N bajo umbral**: conteo de filas con markup < 40% (en alerta
  amarilla o roja). Este número es CLICKABLE — al hacer click filtra
  la tabla para mostrar solo los productos con alerta.

### 3. Barra de controles (una sola línea)

De izquierda a derecha:
- **Campo de búsqueda**: filtra por nombre de producto mientras el
  usuario escribe (HTMX o Alpine.js, lo que sea más consistente con
  el patrón ya usado en la UI). Placeholder: "Search products..."
- **Filtro de categoría**: dropdown multi-select con las categorías
  disponibles en la tienda seleccionada. Permite filtrar por una o más
  categorías simultáneamente.
- **Botón "Export CSV"**: alineado a la derecha. Exporta exactamente
  lo que está visible en la tabla (respetando filtros y búsqueda
  activos).

### 4. Tabla principal

**Agrupada por categoría por defecto.** Cada categoría tiene un header
de grupo con el nombre de la categoría y el conteo de productos en ese
grupo. Las filas de productos van debajo del header de su categoría.

**Una fila por combinación producto + talla.** Si Cappuccino tiene
Small, Medium, y Large, son tres filas separadas, todas bajo el mismo
header de categoría.

#### Columnas

| Columna | Descripción | Ordenable |
|---|---|---|
| Product | Nombre del producto | Sí |
| Size | Nombre de la talla (Small / Medium / Large / Standard / etc.) | Sí |
| Cost | effective_cost en USD con 2 decimales ($X.XX) | Sí |
| Price | precio final en USD con 2 decimales ($X.XX) | Sí |
| Markup | (precio/costo - 1) × 100, con 1 decimal (X.X%) | Sí |
| GM | (precio - costo)/precio × 100, con 1 decimal (X.X%) | Sí |
| Status | Badge de alerta visual (ver sección de alertas) | Sí (por severidad) |

#### Ordenamiento

Cada columna es ordenable con click en el header — toggle
ascendente/descendente. **El agrupado por categoría se mantiene al
ordenar**: se ordena dentro de cada grupo, no se destruye la
agrupación. Excepción: si el usuario ordena por "Product", el
agrupado se puede colapsar opcionalmente para mostrar una vista flat
ordenada alfabéticamente.

#### Alertas visuales por fila — columna Status

Usar los mismos umbrales que ya existen en el Pricing Manager para
consistencia:

| Condición (sobre Markup) | Color de fila | Badge de Status |
|---|---|---|
| Markup ≥ 40% | Normal (sin resalte) | Badge verde "OK" |
| 20% ≤ Markup < 40% | Fondo amarillo suave | Badge amarillo "Watch" |
| Markup < 20% | Fondo rojo suave | Badge rojo "Alert" |

El color de fila es un tint suave del fondo (no texto coloreado) para
que el contenido siga siendo legible. La columna Status tiene el badge
con texto corto.

Adicionalmente, si el producto tiene algún ingrediente con proveedor
placeholder ("Unassigned — Pending Sourcing"), mostrar un badge amber
secundario "No supplier" en la misma celda de Status, junto al badge
de margen. Esto usa el mismo indicador visual que ya se implementó en
el cost calculator breakdown.

---

## Comportamiento de exportación

Al hacer click en "Export CSV":
- Exporta **exactamente lo que está visible** en la tabla en ese
  momento — si hay una búsqueda activa o un filtro de categoría
  aplicado, el CSV solo contiene esas filas.
- Si no hay ningún filtro, exporta todos los productos de la tienda.
- **Columnas del CSV**: `store`, `category`, `product`, `size`,
  `cost_usd`, `price_usd`, `markup_pct`, `gross_margin_pct`, `status`
- La columna `store` al inicio es importante: cuando el archivo se
  comparte fuera de la app (en una reunión con Santiago), queda claro
  de qué tienda son los datos.
- Nombre del archivo: `qargo_pricing_{store_code}_{YYYY-MM-DD}.csv`
  donde `store_code` es el código de la tienda (ej: `2-FV-CA`).

---

## Fuente de datos

**Esta vista NO recalcula costos. Solo lee datos ya calculados.**

```sql
-- Fuente principal
SELECT
    p.name              AS product,
    ps.size_name        AS size,
    pp.calculated_cost  AS cost,
    pp.final_price      AS price,
    pp.currency_code,
    c.name              AS category,
    pp.store_id,
    pp.product_id,
    pp.size_id
FROM product_pricing pp
JOIN products p      ON p.id = pp.product_id
JOIN product_sizes ps ON ps.id = pp.size_id
JOIN categories c    ON c.id = p.category_id
WHERE pp.store_id = :store_id
  AND p.is_active = true
ORDER BY c.name, p.name, ps.size_name
```

Markup y GM se calculan en Python al construir la respuesta, no en
el frontend — consistente con el patrón del Pricing Manager.

Para el badge "No supplier": un ingrediente tiene proveedor placeholder
si tiene una `supply_route` con `is_active = false` y
`manufacturer.name = 'Unassigned — Pending Sourcing'`. Esto ya está
resuelto en el backend — usar la misma lógica que `_result.html` para
detectar el `price_source` de los ingredientes de cada producto.

**Si la tienda no tiene precios calculados** (product_pricing vacío
para ese store_id): mostrar un estado vacío con el mensaje "No pricing
data for this store" y un botón "Calculate prices" que dispara
`POST /api/pricing/calculate-all?store_id={id}`.

---

## Lo que esta vista NO hace — decisiones explícitas

Estas restricciones ya fueron confirmadas con el negocio. No las
implementes aunque parezca natural hacerlo:

- **No edita precios.** Para editar se usa el Pricing Manager
  existente (`/pricing/manager`). Esta vista es lectura + exportación
  solamente. No hay inputs de precio en esta pantalla.
- **No muestra la ruta de suministro.** Eso vive en el tab
  "Active Routes" del detalle de tienda.
- **No tiene vista "un producto en todas las tiendas".** Ese es un
  caso de uso diferente que se puede agregar después como segunda
  pestaña. Por ahora solo existe la vista "todos los productos de una
  tienda".
- **No recalcula.** Lee `product_pricing`. Si los datos están
  desactualizados, el usuario debe ir al Pricing Manager o al botón
  de estado vacío.

---

## Especificación técnica de implementación

### Archivos nuevos a crear

```
backend/routers/pricing_overview_ui.py     ← router principal
templates/pricing_overview/
    overview.html                          ← página completa
    _table.html                            ← partial HTMX: tabla
    _summary.html                          ← partial HTMX: panel resumen
    _export.py  (o endpoint en el router)  ← generación del CSV
```

### Endpoints necesarios

```
GET  /pricing/overview
     → overview.html con lista de tiendas, sin datos aún

GET  /pricing/overview?store_id={id}
     → overview.html con tienda preseleccionada + datos cargados

GET  /pricing/overview/table?store_id={id}&search={q}&category={c}
     → _table.html (partial HTMX, llamado al cambiar tienda/filtros)

GET  /pricing/overview/summary?store_id={id}
     → _summary.html (partial HTMX, llamado al cambiar tienda)

GET  /pricing/overview/export?store_id={id}&search={q}&category={c}
     → StreamingResponse CSV

GET  /stores/{id}/pricing-overview
     → redirige a /pricing/overview?store_id={id}
     (o renderiza directamente como tab dentro de stores/detail.html)
```

### Manejo de errores

Seguir el mismo patrón de los demás módulos de la UI: errores inline,
no páginas de error. Si `store_id` no existe o no tiene datos,
mostrar el estado vacío correspondiente (no un 404 crudo).

### Paginación

Con 168 productos × múltiples tallas = potencialmente 300-400 filas,
implementar paginación de 50 filas por página (o scroll infinito si
es más natural con HTMX). El export CSV siempre descarga todo, sin
paginación.

### Notas de consistencia con el resto de la UI

- Usar los mismos colores de badge que el Pricing Manager: verde para
  OK, amarillo para Watch, rojo para Alert.
- El formato de moneda sigue el patrón ya implementado: USD con 2
  decimales, locale en-US.
- El campo de búsqueda debe hacer debounce (300ms) antes de disparar
  el request HTMX — evitar una request por tecla.
- La exportación CSV usa el mismo enfoque que cualquier StreamingResponse
  ya existente en el proyecto (si no existe ninguna todavía, implementar
  con `fastapi.responses.StreamingResponse` + `io.StringIO`).

---

## Criterios de aceptación

La implementación está completa cuando:

1. Desde el menú principal, puedo seleccionar cualquiera de las 17
   tiendas y ver la tabla con todos sus productos agrupados por
   categoría.
2. Puedo ordenar por cualquier columna sin perder el agrupado por
   categoría (ordena dentro de cada grupo).
3. El panel de resumen muestra markup prom, GM prom, y conteo de
   productos bajo umbral — y al hacer click en ese conteo, la tabla
   se filtra para mostrar solo los problemáticos.
4. La búsqueda por nombre de producto funciona con debounce y filtra
   en tiempo real sin recargar la página.
5. El botón Export CSV descarga un archivo con el nombre correcto
   (`qargo_pricing_{store_code}_{fecha}.csv`) conteniendo exactamente
   lo que está visible en la tabla.
6. Desde el detalle de una tienda (`/stores/{id}`), aparece el tab
   "Pricing Overview" y al hacer click carga la misma vista con esa
   tienda preseleccionada.
7. Los badges de alerta (verde/amarillo/rojo) son visualmente
   distinguibles y consistentes con los umbrales del Pricing Manager
   (40% y 20% de markup).
8. El badge "No supplier" aparece correctamente para productos con
   ingredientes placeholder.
9. Si una tienda no tiene datos calculados, muestra el estado vacío
   con el botón de calcular.
10. No hay ningún input de precio en esta pantalla — es read-only.
