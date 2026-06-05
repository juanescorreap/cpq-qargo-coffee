# Cómo cargar datos — guía práctica

> Paso a paso de cero a primer costo real. Para el detalle técnico (auditoría,
> DAG, diccionario de datos, dead-letter) ver `INGESTION_GUIDE.md`.

## Dónde están los archivos

- `data/raw/*.xlsx` → núcleo (ingredientes, productos, recetas). Abrir con
  Excel / LibreOffice.
- `data/templates/*.csv` → cadena de suministro. Abrir con Excel / LibreOffice
  o editor de texto.

⚠️ Todos los comandos conectan a **producción** (Supabase). Correrlos = escribir
en prod. Llena con datos reales y borra las filas de ejemplo antes de cargar.

---

## Camino mínimo → primer costo correcto

Basta para que el motor calcule bien (sin routing por tienda).

### 1. Catálogos base (una sola vez)

```bash
cd ~/cpq-qargo-coffee
./.venv/bin/python -m backend.migrations.seed_data
```

Crea `recipe_units`, `stores`, `category_margins`.

### 2. Llenar `data/raw/ingredients.xlsx`

| name | category | purchase_unit | **purchase_price** | usage_unit | conversion_factor | yield_% | canonical_unit | supplier_url |
|---|---|---|---|---|---|---|---|---|
| Whole milk | dairy | 1L | **4200** | ml | 1000 | 0.98 | ml | |
| Espresso | coffee | 1kg | **52000** | g | 1000 | 1.0 | g | |

- **`purchase_price` es lo crítico**: sin él, el costo sale 0 sin avisar.
- `conversion_factor` = cuántas `usage_unit` por `purchase_unit` (1 L = 1000 ml).
- `yield_%`: usar fracción 0–1 (0.98 = 98 %).

### 3. Llenar `data/raw/products.xlsx`

| name | category_slug | base_size_oz | prep_time_min | labor_cost_per_min | is_sub_recipe |
|---|---|---|---|---|---|
| Caffe Latte | bebidas-calientes | 12 | 2.5 | 120 | false |

⚠️ `category_slug` debe ser un **slug existente** en `categories`
(ej. `bebidas-calientes`), o la FK queda vacía.

### 4. Llenar `data/raw/sizes.xlsx` y `data/raw/recipes.xlsx`

**sizes.xlsx**

| product_name | size | volume_oz | scale_factor | is_default |
|---|---|---|---|---|
| Caffe Latte | Medium | 12 | 1.0 | true |

**recipes.xlsx**

| product_name | ingredient_name | quantity | recipe_unit | scales_with_size | process_yield_% |
|---|---|---|---|---|---|
| Caffe Latte | Whole milk | 240 | | true | 0 |
| Caffe Latte | Espresso | 2 | shot | false | 0 |

- `quantity` = número solo. `recipe_unit` = columna aparte (vacía → cantidad en
  la `usage_unit` del ingrediente). Si pones `recipe_unit` (ej. `shot`), debe
  tener conversión en `conversions.xlsx`.

### 5. Cargar el núcleo

```bash
./.venv/bin/python -m backend.migrations.migrate_from_excel
```

Lee los 5 xlsx en orden. Filas malas → warning + skip en consola.

### 6. Verificar

```bash
curl -u USER:PASS -X POST \
  https://cpq-qargo-coffee-production.up.railway.app/api/costs/calculate \
  -H 'Content-Type: application/json' -d '{"product_id": 1}'
```

Costo > 0 con ingredientes ≠ 0 → motor funciona.

---

## Camino completo → cadena de suministro

Solo si quieres precios por ruta / región / tienda. Llenas `data/templates/*.csv`.

### 7. Validar SIEMPRE antes de cargar (no escribe nada)

```bash
./.venv/bin/python -m backend.migrations.preflight_check data/templates/ --all
```

Lectura de salida:

- `✅ OK=n REJECT=0` → cargable.
- `⚠️ REJECT=m → data/_rejects/<x>.rejects.csv` → abre ese archivo; la columna
  `reject_reason` dice el porqué. Corrige el dato (o agrega la unidad/región
  faltante al catálogo) y re-valida. Repite hasta `REJECT=0`.

### 8. Cargar cadena de suministro

```bash
./.venv/bin/python -m backend.migrations.migrate_from_templates
```

- Carga en orden DAG (regiones → fabricantes → distribuidores → … → precios →
  asignaciones).
- Error por fila → dead-letter (`data/_rejects/`), el batch sigue.
- Re-correr salta duplicados (idempotente).
- Un solo archivo: `--only regions`. Estricto: `--strict`.

---

## Orden de carga (no saltarse)

```
0. seed_data            recipe_units, stores, category_margins
1. ingredients.xlsx     (CON precios)
2. products.xlsx
3. sizes.xlsx · conversions.xlsx · recipes.xlsx     → migrate_from_excel
4. templates/*.csv      → preflight_check → migrate_from_templates
```

No se puede cargar recetas antes que productos+ingredientes, ni precios de ruta
antes que las rutas. El orden lo respetan los scripts internamente, pero los
nombres referenciados deben existir.

---

## Reglas de oro al llenar

1. **Nombres exactos.** `ingredient_name` en CSV = `name` en `ingredients.xlsx`.
   El loader matchea por nombre (ignora mayúsculas/espacios extra).
2. **Clave de ruta** = `(ingredient_name, manufacturer_name, distributor_name)`.
   Distribuidor vacío = compra directa al fabricante.
3. **Precio siempre con `currency_code`** (`COP` / `USD` / `EUR`).
   `qargo_price ≤ list_price`.
4. **Fechas** `YYYY-MM-DD`, booleanos `true`/`false`, celda vacía = NULL.
5. **Borra las filas de ejemplo** (Alpina/Monin/regiones) antes de cargar, o
   entran a producción.

---

## Solución de problemas

| Síntoma | Causa | Arreglo |
|---|---|---|
| Costo del producto ≈ solo labor | ingrediente sin precio | llenar `purchase_price` en `ingredients.xlsx` o `ingredient_prices.csv` |
| `404 ingredient/product not found` al calcular | nombre no coincide | revisar `ingredient_name`/`product_name` exactos |
| Receta no aparece | unidad sin conversión | agregar fila en `conversions.xlsx` |
| Fila en `data/_rejects/` | dato sucio / FK faltante | leer `reject_reason`, corregir, re-correr |
| `recipe_unit 'x' no existe` | falta correr seed | `python -m backend.migrations.seed_data` |
| `category` FK vacía | no es slug | usar slug existente en `categories` |

---

## Comandos de referencia rápida

```bash
# catálogos base (1 vez)
./.venv/bin/python -m backend.migrations.seed_data

# núcleo (xlsx)
./.venv/bin/python -m backend.migrations.migrate_from_excel

# validar templates (dry-run)
./.venv/bin/python -m backend.migrations.preflight_check data/templates/ --all

# cargar cadena de suministro (csv)
./.venv/bin/python -m backend.migrations.migrate_from_templates

# verificar cálculo
curl -u USER:PASS -X POST \
  https://cpq-qargo-coffee-production.up.railway.app/api/costs/calculate \
  -H 'Content-Type: application/json' -d '{"product_id": <ID>}'
```
