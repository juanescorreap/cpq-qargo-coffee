# Diagnóstico de gaps de precio — Edinburgo

> Copia y pega esto en Claude Code. Es un diagnóstico de SOLO LECTURA
> para entender el estado real de Edinburgo antes de diseñar el flujo
> de costos. No modifica nada.

---

```
Necesito un diagnóstico completo del estado de costos para la tienda
de Edinburgo (TX). El objetivo es saber exactamente qué falta para
poder correr el pricing engine y obtener costos reales y confiables
para esa tienda.

═══════════════════════════════════════════════════════════════════
PASO 1 — Identificar la tienda de Edinburgo
═══════════════════════════════════════════════════════════════════
  1. SELECT id, name, code, region_id, default_currency_code
     FROM stores WHERE name ILIKE '%edinburg%' OR code ILIKE '%edinburg%';
  2. Confirma su catalog_store_id en store_catalog_mapping.
  3. Confirma el status del último sync en catalog_sync_log para
     esa tienda (items_fetched, items_matched, items_created,
     items_updated, status).

═══════════════════════════════════════════════════════════════════
PASO 2 — Ingredientes usados en recetas activas
═══════════════════════════════════════════════════════════════════
Lista todos los ingredientes únicos que aparecen en recipe_ingredients
de productos activos (is_active=true). Esta es la lista completa de
lo que el motor de costos necesita para calcular cualquier producto
del menú:

  SELECT DISTINCT
      i.id,
      i.name,
      i.purchase_price,
      i.purchase_unit,
      i.canonical_unit
  FROM recipe_ingredients ri
  JOIN products p ON p.id = ri.product_id AND p.is_active = true
  JOIN ingredients i ON i.id = ri.ingredient_id AND i.is_active = true
  ORDER BY i.name;

Reporta el conteo total.

═══════════════════════════════════════════════════════════════════
PASO 3 — Estado de precio por ingrediente (los 4 estados posibles)
═══════════════════════════════════════════════════════════════════
Para cada ingrediente del Paso 2, clasifícalo en uno de estos estados:

  A) PRECIO_VIA_RUTA — tiene supply_route activa + supply_route_price
     vigente para Edinburgo (store_id o region_id que cubra Edinburgo)
     → el motor de costos usa este precio con máxima precisión

  B) PRECIO_VIA_CATALOGO — llegó precio de la API en el sync de
     Edinburgo (catalog_match_log con action_taken en ('created',
     'price_updated') del sync de esa tienda) Y tiene purchase_price
     en ingredients, pero NO tiene supply_route activa
     → el motor usa purchase_price como fallback, precio existe pero
       sin trazabilidad de ruta

  C) PRECIO_FALLBACK — tiene purchase_price en ingredients (viene del
     Excel original de carga), pero NO llegó precio del sync de
     Edinburgo específicamente
     → el motor usa purchase_price, precio puede estar desactualizado

  D) SIN_PRECIO — ni supply_route_price ni purchase_price
     → el motor devuelve $0 para este ingrediente, costo subestimado

Reporta la tabla completa con cada ingrediente y su estado, y el
conteo por categoría (cuántos en A, B, C, D).

═══════════════════════════════════════════════════════════════════
PASO 4 — Impacto por producto
═══════════════════════════════════════════════════════════════════
Para los ingredientes en estado D (SIN_PRECIO), lista qué productos
del menú los usan y en qué proporción impactan el costo total de ese
producto:

  Por cada producto que usa al menos un ingrediente D:
  - Nombre del producto
  - Cuántos de sus ingredientes son D vs. total
  - Estimación del % del costo que representa ese ingrediente
    (si tienes el costo de los otros ingredientes)

Esto permite priorizar: si un ingrediente D representa el 2% del
costo de un producto, es menos urgente que uno que representa el 60%.

═══════════════════════════════════════════════════════════════════
PASO 5 — Ingredientes pendientes de mapeo de Edinburgo
═══════════════════════════════════════════════════════════════════
De los 236 ingredientes en "Pending review" (created por catalog sync),
¿cuántos vienen específicamente del sync de Edinburgo?

  SELECT COUNT(DISTINCT cml.matched_ingredient_id)
  FROM catalog_match_log cml
  JOIN catalog_sync_log csl ON csl.id = cml.sync_log_id
  JOIN stores s ON s.id = csl.store_id
  WHERE s.name ILIKE '%edinburg%'
    AND cml.action_taken = 'created';

Y de esos, ¿cuántos corresponden a ingredientes que aparecen en
recetas activas (es decir, si se mapearan correctamente, mejorarían
directamente el cálculo de costos)?

═══════════════════════════════════════════════════════════════════
FORMATO DEL REPORTE
═══════════════════════════════════════════════════════════════════
  1. Store info + sync status de Edinburgo
  2. Total ingredientes en recetas activas
  3. Tabla de estados A/B/C/D con conteos
  4. Lista de productos afectados por ingredientes D (si los hay)
  5. Pendientes de mapeo de Edinburgo y su impacto directo en recetas

NO modifiques ningún dato. Solo diagnóstico.
```
