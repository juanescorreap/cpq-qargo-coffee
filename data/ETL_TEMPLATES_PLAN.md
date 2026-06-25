# Plan ETL — Derivación de Templates de Cadena de Suministro
**Proyecto:** Qargo Coffee · Pipeline de costeo y logística  
**Fecha:** 2026-06-19  
**Alcance:** Templates derivables de `data/files-to-explore/` hacia `data/templates/`

---

## Contexto Operativo

Qargo Coffee opera **17 tiendas activas en Estados Unidos** (CA, FL, TX, MI, OH, IL, NV, DC).  
Todos los precios en los archivos fuente están en **USD**.  
Los templates con `currency_code=COP` / `country_code=CO` corresponden a expansión Colombia — **la carga inicial usa USD**.

### Archivos fuente (`data/files-to-explore/`)

| Archivo | Contenido clave |
|---|---|
| `lista de tiendas.xlsx` | Catálogo oficial de 17 tiendas: código, ciudad, estado, dueño |
| `2025_Q4 - Bridor Rebate Report.xlsx` | Compras Q4-2025: fabricante Bridor, distribuidor Greco & Sons, SKUs, precios |
| `Comparison Bindi - Report (6_19_2026).xlsx` | Compras 2026: Bindi + distribuidores alternativos, SKUs, precios por case y por unit |
| `PreGel Consolidate 2026.xlsx` | Compras 2026: ingredientes de gelato PreGel, SKUs, precios |
| `Goolden Waffles Consolidate 2026.xlsx` | Compras 2026: Golden Waffles, SKUs, precios |
| `Kimbo Stores.xlsx` | Inventario semanal de café Kimbo por tienda |
| `General Sales Control.xlsx` | Ventas mensuales por tienda (POS granular) |
| `Stores Info 2026.xlsx` | Estado operativo de tiendas, Site IDs |
| `Existing & New Store Equipment - KIMBO & FETCO.xlsx` | Equipamiento por tienda, mantenimiento |

---

## Principios de Normalización Globales

```
STRINGS:      Title Case; strip espacios; sin caracteres especiales en codes
CODES:        MAYÚSCULAS, sin espacios, guión como separador (CA, FL, QC-001)
PRECIOS:      float con 4 decimales; sin símbolo de moneda; punto como decimal
FECHAS:       ISO 8601: YYYY-MM-DD
BOOLEANS:     true / false (minúsculas)
CURRENCY:     ISO 4217: USD (operación actual USA)
NULL:         celda vacía; nunca string "null", "N/A", "#N/A"
ENCODING:     UTF-8
```

---

## Template 1 — `regions.csv`

**Fuente:** `lista de tiendas.xlsx / STORES` → col `STATE`

### Estándar de campos

| Campo | Tipo | Estándar | Regla |
|---|---|---|---|
| `name` | str | Title Case, nombre completo del estado | "CA" → "California" |
| `code` | str | 2–3 chars, MAYÚSCULAS | Código postal USPS del estado |
| `country_code` | str | ISO 3166-1 alpha-2 | Constante `"US"` |
| `is_active` | bool | `true` / `false` | Constante `true` |

### Transformación

```
1. Leer col STATE de STORES → extraer valores únicos
2. Normalizar "D.C." → code="DC", name="Washington D.C."
3. Mapear STATE → nombre completo:
   CA → California
   FL → Florida
   TX → Texas
   MI → Michigan
   OH → Ohio
   IL → Illinois
   NV → Nevada
   DC → Washington D.C.
4. Deduplicar por code
5. Escribir 8 filas en regions.csv
```

### Output esperado

```csv
name,code,country_code,is_active
California,CA,US,true
Florida,FL,US,true
Texas,TX,US,true
Michigan,MI,US,true
Ohio,OH,US,true
Illinois,IL,US,true
Nevada,NV,US,true
Washington D.C.,DC,US,true
```

---

## Template 2 — `stores_regions.csv`

**Fuente:** `lista de tiendas.xlsx / STORES` → cols `NEW CODE` + `STATE`

### Estándar de campos

| Campo | Tipo | Estándar | Regla |
|---|---|---|---|
| `store_code` | str | Formato `N-XX-ST` | Usar `NEW CODE` tal cual (1-FV-CA, 2-LB-CA…) |
| `region_code` | str | 2–3 chars MAYÚSCULAS | STATE normalizado (DC para "D.C.") |

### Transformación

```
1. Leer STORES: NEW CODE → store_code; STATE → region_code
2. Normalizar STATE: "D.C." → "DC"
3. Filtrar STATUS == "OPEN" (todos lo son)
4. Output: 17 filas, una por tienda
```

### Output esperado

```csv
store_code,region_code
1-FV-CA,CA
2-LB-CA,CA
3-TM-FL,FL
4-WC-DC,DC
5-BK-CA,CA
6-DT-MI,MI
7-ED-TX,TX
8-WV-OH,OH
10-BL-IL,IL
11-SA-TX,TX
12-DB-MI,MI
13-BL-IL,IL
14-SC-IL,IL
15-OP-IL,IL
16-GP-TX,TX
17-VG-NV,NV
18-CN-MI,MI
```

---

## Template 3 — `manufacturers.csv`

**Fuentes:**
- `Bridor Rebate / Data` → col `Brand`
- `Bindi / Data` → col `Group` (filtrar solo fabricantes)
- `Bindi / Revised Prod Database` → col `Group` = "Coffee" → Kimbo
- Filenames: `PreGel Consolidate 2026.xlsx`, `Goolden Waffles Consolidate 2026.xlsx`

### Estándar de campos

| Campo | Tipo | Estándar | Regla |
|---|---|---|---|
| `name` | str | Title Case, nombre legal completo | Sin abreviaturas: "BRIDOR" → "Bridor" |
| `country_code` | str | ISO 3166-1 alpha-2 | Ver tabla de mapeo manual |
| `tax_id` | str | Formato libre | **GAP** — dejar vacío |
| `website` | str | URL completa con `https://` | **GAP** — dejar vacío |
| `is_active` | bool | `true` / `false` | Constante `true` |

### Transformación

```
1. Extraer valores únicos de:
   - Bridor/Data col Brand: "BRIDOR" → "Bridor"
   - Bindi/Data col Group → filtrar solo fabricantes (no distribuidores):
       "Bindi" → mantener como fabricante
       "Coffee" (de Revised DB) → "Kimbo" (inferido por contexto del archivo)
   - Filename PreGel → fabricante = "PreGel"
   - Filename Goolden Waffles → fabricante = "Golden Waffles"

2. Tabla de normalización:
   Raw value          → name             → country_code
   "BRIDOR"           → "Bridor"         → "FR"
   "Bindi"            → "Bindi"          → "IT"
   "Coffee" (Revised) → "Kimbo"          → "IT"
   PreGel (filename)  → "PreGel"         → "IT"
   Goolden Waffles    → "Golden Waffles" → "US"

3. EXCLUIR del CSV de manufacturers los distribuidores identificados:
   "Food Related", "French Bakery", "Greco", "Greco & Sons",
   "Pointe Dairy", "TCW", "Primizie", "IGF"
   (estos van a distributors.csv)
```

---

## Template 4 — `distributors.csv`

**Fuentes:**
- `Bridor Rebate / Data` → col `Distributor`
- `Bindi / Data` → col `Group` (valores que NO son fabricantes)

### Estándar de campos

| Campo | Tipo | Estándar | Regla |
|---|---|---|---|
| `name` | str | Title Case completo | "GRECO & SONS" → "Greco & Sons" |
| `country_code` | str | ISO 3166-1 alpha-2 | Constante `"US"` (todos operan en EE.UU.) |
| `tax_id` | str | EIN formato XX-XXXXXXX | **GAP** — dejar vacío |
| `contact_email` | str | formato email válido | **GAP** — dejar vacío |
| `contact_phone` | str | formato `+1-XXX-XXX-XXXX` | **GAP** — dejar vacío |
| `is_active` | bool | `true` / `false` | Constante `true` |

### Transformación

```
1. Extraer todos los valores de:
   - Bridor/Data col Distributor:
     {"Greco & Sons", "Food Related", "TCW", "French Bakery", "Pointe Dairy"}
   - Bindi/Data col Group → excluir fabricantes conocidos:
     {"Food Related", "French Bakery", "Greco", "Pointe Dairy", "IGF", "Primizie"}

2. Deduplicar y normalizar nombres:
   Raw value         → name normalizado
   "Greco & Sons"    → "Greco & Sons"
   "Greco"           → "Greco & Sons"   ← fusionar variante
   "Food Related"    → "Food Related"
   "French Bakery"   → "French Bakery"
   "Pointe Dairy"    → "Pointe Dairy"
   "TCW"             → "TCW"
   "IGF"             → "IGF"
   "Primizie"        → "Primizie"

3. Regla de fusión de variantes:
   Detectar similitud con difflib.get_close_matches(cutoff=0.80)
   Si match → usar la versión más larga/completa del nombre
```

---

## Template 5 — `ingredient_prices.csv`

**Fuentes:**
- `Bindi / Products Database` → `Item`, `Price per Unit ($)`, `Group`, `Date`
- `PreGel / Products Database` → `Item`, `Price per Unit ($)`, `Date`
- `Goolden Waffles / Products Database` → `Item`, `Price per Unit ($)`, `Date`
- `Bridor / Data` → `Product Description`, `Total Price`, `Cases Ordered`

### Estándar de campos

| Campo | Tipo | Estándar | Regla |
|---|---|---|---|
| `ingredient_name` | str | Exacto al nombre canónico en `ingredients-v1.xlsx` | Ver crosswalk |
| `purchase_price` | float(4) | Precio por unidad de compra, sin símbolo | `Price per Unit ($)` directo; Bridor: `Total Price / Cases Ordered` |
| `currency_code` | str | ISO 4217 | Constante `"USD"` |
| `source` | str | `snake_case`, sin espacios | `"bindi_2026"`, `"pregel_2026"`, `"golden_waffles_2026"`, `"bridor_q4_2025"` |
| `effective_date` | date | YYYY-MM-DD | `MAX(Date)` por ingrediente; Bridor: `"2025-10-01"` (inicio Q4) |

### Transformación — Crosswalk de nombres (crítica)

```
PROBLEMA: nombres en fuentes son del catálogo del proveedor, no del canónico Qargo.
Ejemplos:
  "0026P - CHEESECAKE ALLE FRAGOLE " → posible canonical: "Cheesecake Pistachio"
  "Chocolate Sprint"                  → posible canonical: "Chocolate Gelato"
  "20-Cinnamon Roll Flavor Pack"     → posible canonical: no existe

REGLA DE CROSSWALK (3 pasos):
  Paso 1 — Limpiar nombre externo:
    a) Strip código de prefijo: regex r"^[\w\-\.]+\s*[-–]\s*" elimina "0026P - " o "20-"
    b) Strip espacios; Title Case
    c) Resultado: external_name_clean

  Paso 2 — Match exacto (case-insensitive) contra ingredients-v1.xlsx col name
    Si match → ingredient_name = canonical

  Paso 3 — Si no hay match exacto → fuzzy match con difflib.get_close_matches
    cutoff = 0.72; n = 1
    Si score < 0.72 → marcar como UNMATCHED → requiere revisión manual

CAMPOS ADICIONALES:
  - price_per_unit = Price per Unit ($) si existe, sino: Total Price / Cases Ordered
  - Redondear a 4 decimales: round(price, 4)
  - Filtrar filas donde price <= 0 o es nulo
  - Por cada (ingredient_name, source) → usar fila con MAX(Date)
  - Si mismo ingrediente aparece en múltiples tiendas → tomar mediana de precios
```

### Función de limpieza de precios

```python
def clean_price(val) -> float | None:
    if val is None:
        return None
    s = str(val).strip().replace("$", "").replace(",", ".")
    try:
        f = float(s)
        return round(f, 4) if f > 0 else None
    except ValueError:
        return None
```

---

## Template 6 — `supply_routes.csv`

**Fuentes:** Cruce de todas las fuentes (fabricante + ingrediente + distribuidor)

### Estándar de campos

| Campo | Tipo | Estándar | Regla |
|---|---|---|---|
| `ingredient_name` | str | Canónico (crosswalk) | Mismo proceso que ingredient_prices |
| `manufacturer_name` | str | Exacto a manufacturers.csv | Match por nombre normalizado |
| `distributor_name` | str \| null | Exacto a distributors.csv | Null si `is_direct=true` |
| `is_direct` | bool | `true` / `false` | Bindi: DSD Price NOT NULL Y DSD == Price_per_case → `true` |
| `is_active` | bool | `true` | Constante |

### Transformación

```
FUENTE BINDI:
  Por cada fila en Products Database donde ingredient matchea canónico:
    manufacturer_name = "Bindi"  (cuando Group == "Bindi" o DSD existe)
    distributor_name  = Group    cuando Group != "Bindi" ELSE null
    is_direct         = true     cuando DSD Price == Price per case (compra directa)
                      = false    cuando hay distribuidor diferente

FUENTE BRIDOR:
  manufacturer_name = "Bridor"
  distributor_name  = Bridor/Data col Distributor (normalizado)
  is_direct         = false (siempre hay distribuidor en el reporte)

FUENTE PREGEL:
  manufacturer_name = "PreGel"
  distributor_name  = null  ← GAP, no identificado en archivos
  is_direct         = null  ← pendiente

FUENTE GOLDEN WAFFLES:
  manufacturer_name = "Golden Waffles"
  distributor_name  = null  ← GAP
  is_direct         = null  ← pendiente

DEDUPLICACIÓN:
  UNIQUE(ingredient_name, manufacturer_name, distributor_name)
  Si mismo ingrediente tiene múltiples rutas (diferentes distribuidores)
  → crear UNA FILA POR COMBINACIÓN (cada combinación es una ruta distinta)
```

---

## Template 7 — `ingredient_supplier_refs.csv`

**Fuentes:**
- `Bindi / Products Database` → `Code`, `Item`, `Group`, `Bags/Units Per Case`
- `Bindi / Revised Prod Database` → `Code`, `Item`, `Kg Per Case`, `Pounds Per Case`, `Bags/Units Per Case`
- `PreGel / Products Database` → `Code`, `Item`, `Bags/Units Per Case`
- `Goolden Waffles / Products Database` → `Code`, `Item`, `Bags/Units Per Case`
- `Bridor / Data` → `Item Number`, `Product Description`

### Estándar de campos

| Campo | Tipo | Estándar | Regla |
|---|---|---|---|
| `ingredient_name` | str | Canónico | Crosswalk desde `Item` / `Product Description` |
| `manufacturer_name` | str | Exacto a manufacturers.csv | Por fuente |
| `distributor_name` | str \| null | Exacto a distributors.csv | Bindi: `Group` cuando es distribuidor |
| `external_name` | str | Tal como aparece en catálogo del proveedor | `Item` limpio de prefijo de código |
| `external_code` | str \| null | Tal como aparece en catálogo | `Code` / `Item Number` como string |
| `purchase_unit` | str | Descripción legible del empaque | Inferida (ver regla) |
| `units_per_pack` | float | Número positivo | `Bags/Units Per Case` directo |

### Transformación

```
LIMPIEZA DE external_name:
  Patrón: "0026P - CHEESECAKE ALLE FRAGOLE " → strip code prefix
  regex: re.sub(r'^[\w\-\.]+\s*[-–]\s*', '', item).strip().title()
  Resultado: "Cheesecake Alle Fragole"

LIMPIEZA DE external_code:
  Convertir a string: str(Code).strip()
  Si es float con .0 → convertir a int primero: int(Code) si Code == int(Code)
  Ejemplos: 305132.0 → "305132"; "0026P" → "0026P"; 81659.0 → "81659"

INFERENCIA DE purchase_unit (en orden de prioridad):
  Si Gallons_per_Case > 0   → "Case {Bags} × {Gallons}gal"
  Elif Kg_per_Case > 0      → "Case {Bags} × {round(Kg/Bags,2)}kg"
  Elif Pounds_per_Case > 0  → "Case {Bags} × {round(Lb/Bags,2)}lb"
  Else                       → "Case {int(Bags)} units"

DEDUPLICACIÓN:
  UNIQUE(ingredient_name, manufacturer_name, distributor_name, external_code)
  Si mismo SKU aparece con distintos precios por tienda →
    mantener 1 referencia; el precio va a supply_route_prices
```

---

## Template 8 — `supplier_unit_conversions.csv`

**Fuentes:**
- `Bindi / Revised Prod Database` → `Kg Per Case`, `Pounds Per Case`, `Bags/Units Per Case`, `Gallons per Case`
- `PreGel / Products Database` → `Bags/Units Per Case`
- `Goolden Waffles / Products Database` → `Bags/Units Per Case`

### Estándar de campos

| Campo | Tipo | Estándar | Regla |
|---|---|---|---|
| `ingredient_name` | str | Canónico | Crosswalk |
| `manufacturer_name` | str | Exacto | Por fuente |
| `distributor_name` | str \| null | Exacto | Por fuente |
| `recipe_unit` | str | Unidad canónica de receta | Ver tabla de mapeo |
| `purchase_qty` | float | Unidades por case | `Bags/Units Per Case` |
| `recipe_qty` | float | Total en unidad de receta | `Kg × 1000` o `Lb × 453.592` o `Gal × 3785.41` |

### Transformación — Regla de unidad canónica

```
PRIORIDAD: seleccionar la unidad de mayor precisión disponible

  1. Si Gallons_per_Case > 0  → recipe_unit = "ml"; recipe_qty = Gallons × 3785.41
  2. Elif Kg_per_Case > 0     → recipe_unit = "g";  recipe_qty = Kg × 1000
  3. Elif Pounds_per_Case > 0 → recipe_unit = "g";  recipe_qty = Lb × 453.592
  4. Else (solo units)        → recipe_unit = "unit"; recipe_qty = Bags/Units Per Case

VALIDACIÓN:
  recipe_qty > 0
  purchase_qty > 0
  recipe_qty / purchase_qty > 0

CROSS-CHECK contra ingredients-v1.xlsx:
  El recipe_unit derivado debe coincidir con la familia canónica del ingrediente:
    canonical_unit = "L"    → recipe_unit debe ser "ml" o "L"   ✓
    canonical_unit = "kg"   → recipe_unit debe ser "g" o "lb"   ✓
    canonical_unit = "unit" → recipe_unit debe ser "unit"        ✓
  Si no coincide → marcar fila como REVIEW_NEEDED en columna de notas

EJEMPLO:
  Bindi Cheesecake: Bags=14, Kg=5.5, Gallons=0
  → recipe_unit="g", recipe_qty=5500, purchase_qty=14
  → Interpretación: 1 case (14 porciones) = 5500g total = 393g/porción
```

---

## Template 9 — `supply_route_prices.csv`

**Fuentes:**
- `Bindi / Products Database` → `Price per case ($)`, `DSD Price`, `Price per Unit ($)`, `Date`
- `PreGel / Products Database` → `Price per case ($)`, `Price per Unit ($)`, `Date`
- `Goolden Waffles / Products Database` → `Price per case ($)`, `Price per Unit ($)`, `Date`
- `Bridor / Data` → `Total Price`, `Cases Ordered`, `Quarter`

### Estándar de campos

| Campo | Tipo | Estándar | Regla |
|---|---|---|---|
| `ingredient_name` | str | Canónico | Crosswalk |
| `manufacturer_name` | str | Exacto | Por fuente |
| `distributor_name` | str \| null | Exacto | Bindi: Group; Bridor: Distributor |
| `list_price` | float(4) | Precio por case, USD | `Price per case ($)` |
| `qargo_price` | float(4) | Precio negociado Qargo | Ver regla DSD |
| `currency_code` | str | ISO 4217 | Constante `"USD"` |
| `price_unit` | str | Descripción de la unidad | `"per case"` |
| `valid_from` | date | YYYY-MM-DD | `MAX(Date)` por ruta |
| `source` | str | `snake_case` | `"bindi_products_db_2026"` etc. |
| `created_by` | str | usuario o sistema | `"etl_migration_2026"` |

### Transformación — Regla DSD (precio negociado)

```
BINDI tiene dos columnas de precio:
  Price per case ($) = precio de lista del distribuidor
  DSD Price          = precio de entrega directa del fabricante

LÓGICA qargo_price:
  Si DSD Price < Price per case  → qargo_price = DSD Price  (Qargo usa directo)
  Si DSD Price == Price per case → qargo_price = Price per case  (sin descuento)
  Si DSD Price IS NULL           → qargo_price = Price per case  (no hay DSD)

CONSTRAINT HARD: qargo_price <= list_price (siempre; rechazar fila si viola)

BRIDOR — precio inferido:
  list_price  = clean_price(Total Price) / Cases Ordered
  qargo_price = list_price  (no hay precio alternativo en el archivo)
  valid_from  = "2025-10-01"  (inicio Q4-2025)

PREGEL / GOLDEN WAFFLES:
  list_price  = Price per case ($)
  qargo_price = list_price  (no hay precio negociado en archivos)

DEDUPLICACIÓN:
  Por (ingredient_name, manufacturer_name, distributor_name):
    Si hay múltiples fechas → mantener fila con MAX(valid_from)
    Si hay múltiples tiendas mismo periodo → tomar el MENOR qargo_price
    (principio: precio más favorable aplica a toda la red)
```

---

## Template 10 — `supply_route_assignments.csv` (parcial)

**Fuentes:** `Bindi / Data` + hojas por tienda; `Bridor / Data` → col `QC Store`

### Campos derivables

| Campo | Derivable | Transformación |
|---|---|---|
| `scope_type` | ✅ | Constante `"store"` |
| `scope_code` | ✅ | Store name → crosswalk → `NEW CODE` de lista de tiendas |
| `ingredient_name` | ✅ (parcial) | Crosswalk desde `Item` |
| `manufacturer_name` | ✅ | Por fuente |
| `distributor_name` | ✅ (parcial) | `Group` / `Distributor` column |
| `priority` | ❌ GAP | No derivable — corporativo define |
| `valid_from` | ✅ | `MAX(Date)` por tienda-ingrediente |
| `assigned_by` | constante | `"etl_migration_2026"` |

### Transformación — Crosswalk store name → store code

```
PROBLEMA: archivos usan nombres como "Long Beach", "Fountain Valley"
          lista de tiendas usa "1-FV-CA", "2-LB-CA"

TABLA DE CROSSWALK:
  "Long Beach"        → "2-LB-CA"
  "Fountain Valley"   → "1-FV-CA"
  "Tampa"             → "3-TM-FL"
  "Farragut"          → "4-WC-DC"  (Farragut = Washington DC)
  "Berkeley"          → "5-BK-CA"
  "Detroit"           → "6-DT-MI"
  "Edinburg"          → "7-ED-TX"  (variante: "Edimburg" → mismo)
  "Westerville"       → "8-WV-OH"
  "Meijer 215"        → "10-BL-IL"
  "San Antonio"       → "11-SA-TX"
  "Dearborn"          → "12-DB-MI"
  "Meijer 169"        → "13-BL-IL"
  "Meijer 182"        → "14-SC-IL"
  "Orland Park"       → "15-OP-IL"
  "Grand Prairie"     → "16-GP-TX"

FILTRAR: tiendas no reconocidas (Santa Monica, San Jose, Denver,
  West Palm Beach, Cooper City, St Pete) eran tiendas en transición.
  SKIP estas filas — no están en los 17 stores activos.

INFERENCIA DE PRIORIDAD (provisional):
  Si el mismo ingrediente tiene DOS rutas para la misma tienda:
    - Ruta con menor qargo_price → priority = 1 (primaria)
    - Ruta con mayor qargo_price → priority = 2 (alternativa)
  NOTA: Esta asignación es PROVISIONAL y debe ser confirmada por corporativo.
```

---

## Secuencia de Ejecución

```
FASE 0 — Pre-proceso (sin dependencias):
  Construir crosswalk: external_name → canonical ingredient_name
    Input  : todos los Item / Product Description únicos de fuentes
    Output : crosswalk.csv con columnas:
               external_name | canonical_name | match_score | status
    Herramienta: Python difflib.get_close_matches(cutoff=0.72)
    REVISIÓN HUMANA: filas con match_score < 0.85

FASE 1 — Sin dependencias (cargar primero al pipeline):
  → regions.csv              8 filas   100% automático
  → manufacturers.csv        5 filas   ~80% automático
  → distributors.csv         8 filas   ~70% automático

FASE 2 — Depende de FASE 1 + crosswalk:
  → stores_regions.csv            17 filas   100% automático
  → ingredient_prices.csv         ~60% de ingredientes con precio
  → supply_routes.csv             ~60% de rutas identificadas

FASE 3 — Depende de FASE 2:
  → ingredient_supplier_refs.csv   ~60% de ingredientes
  → supplier_unit_conversions.csv  ~50% de ingredientes
  → supply_route_prices.csv        ~60% con precios

FASE 4 — Depende de FASE 3:
  → supply_route_assignments.csv   ~40%, priority provisional
```

---

## Templates con GAP Total (sin fuente)

| Template | Estado | Acción |
|---|---|---|
| `ingredient_substitutes.csv` | ❌ Sin fuente | Levantamiento con corporativo |
| `ingredient_availability.csv` | ❌ Sin fuente (solo Kimbo ~10%) | Dato operativo en tiempo real |
| `supply_route_assignments.priority` | ❌ Parcial | Decisión de corporativo |
| `manufacturers.tax_id / website` | ❌ Sin fuente | Consulta externa (RUES, webs) |
| `distributors.contact_*` | ❌ Sin fuente | Levantamiento con compras |

---

## Validaciones Post-Carga Obligatorias

```sql
-- 1. Todo store_code existe en la tabla stores del DB
SELECT store_code FROM stores_regions
WHERE store_code NOT IN (SELECT code FROM stores);
-- Esperado: 0 filas

-- 2. Todo manufacturer en supply_routes existe en manufacturers
SELECT DISTINCT manufacturer_name FROM supply_routes
WHERE manufacturer_name NOT IN (SELECT name FROM manufacturers);
-- Esperado: 0 filas

-- 3. qargo_price nunca mayor que list_price
SELECT ingredient_name, list_price, qargo_price
FROM supply_route_prices
WHERE qargo_price > list_price;
-- Esperado: 0 filas

-- 4. units_per_pack > 0 siempre
SELECT * FROM ingredient_supplier_refs
WHERE units_per_pack IS NULL OR units_per_pack <= 0;
-- Esperado: 0 filas

-- 5. Todo ingredient_name en templates existe en ingredients tabla
SELECT DISTINCT ingredient_name FROM ingredient_prices
WHERE ingredient_name NOT IN (SELECT name FROM ingredients);
-- Esperado: 0 filas (post-crosswalk)

-- 6. recipe_qty y purchase_qty positivos
SELECT * FROM supplier_unit_conversions
WHERE recipe_qty <= 0 OR purchase_qty <= 0;
-- Esperado: 0 filas
```

---

## Cobertura Estimada por Template

| Template | Cobertura | Fuente primaria |
|---|---|---|
| `stores_regions.csv` | **100%** | `lista de tiendas.xlsx / STORES` |
| `regions.csv` | **100%** | `lista de tiendas.xlsx / STORES` |
| `ingredient_supplier_refs.csv` | ~60% | Products Database (Bindi, PreGel, Golden Waffles, Bridor) |
| `manufacturers.csv` | ~70% | Brand / Group columns + filenames |
| `distributors.csv` | ~65% | Distributor / Group columns |
| `supply_route_prices.csv` | ~60% | Price per case + DSD Price |
| `ingredient_prices.csv` | ~60% | Price per Unit columns |
| `supply_routes.csv` | ~60% | Cruce fabricante + ingrediente + distribuidor |
| `supply_route_assignments.csv` | ~40% | Data sheets por tienda (priority provisional) |
| `supplier_unit_conversions.csv` | ~50% | Revised Prod Database (Bindi) |
| `ingredient_availability.csv` | **<10%** | Kimbo Stores (solo espresso) |
| `ingredient_substitutes.csv` | **0%** | Sin fuente |
