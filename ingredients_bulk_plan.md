# Plan — Exportar, normalizar y reimportar ingredientes

> Documento para Claude Code. Lee esto completo antes de escribir
> cualquier línea de código. Implementar en el orden exacto de los
> 5 pasos.

---

## Contexto

El objetivo es:
1. Exportar todos los ingredientes activos a CSV
2. El usuario corrige conversion factors en Excel
3. Script normaliza nombres e purchase units con reglas fijas
4. El usuario revisa el CSV normalizado
5. Script de carga aplica los cambios a la BD via ingredient_price_history
   para current_price y UPDATE directo para los otros campos

---

## Paso 1 — Script de exportación

### Archivo a crear
```
backend/scripts/export_ingredients.py
```

### Qué exporta
Todos los ingredientes (is_active = true AND false) con estas
columnas exactas en este orden:

```
id, name, category, purchase_price, purchase_unit, usage_unit,
conversion_factor, yield_percentage, current_price, status
```

Donde:
- `status` = 'active' si is_active=true, 'inactive' si false
- `yield_percentage` se exporta como porcentaje (×100):
  yield_percentage=0.98 → columna muestra 98.0
- `current_price` se exporta aunque sea NULL (celda vacía)

### Nombre del archivo de salida
```
data/exports/ingredients_export_YYYY-MM-DD.csv
```

### Comando de ejecución
```bash
python -m backend.scripts.export_ingredients
```

### Criterio de aceptación
- El CSV tiene exactamente las columnas listadas
- Todos los ingredientes activos e inactivos están incluidos
- Los números usan punto decimal (no coma)
- El archivo se crea en data/exports/

---

## Paso 2 — Script de normalización

### Archivo a crear
```
backend/scripts/normalize_ingredients.py
```

### Input
El CSV exportado en el Paso 1 (o cualquier CSV con las mismas
columnas). Se pasa como argumento:
```bash
python -m backend.scripts.normalize_ingredients \
  data/exports/ingredients_export_2026-07-10.csv
```

### Reglas de normalización de nombres (columna `name`)

Aplicar en este orden:

**Regla 1 — Title Case**
Cada palabra en mayúscula inicial, resto en minúscula.
Excepciones que NO se tocan (lista exacta):
- Palabras de artículo/preposición en medio: "and", "or", "of",
  "the", "a", "an", "with", "de", "di", "al", "la", "le"
  → se mantienen en minúscula si NO son la primera palabra
- Siglas conocidas: "RTB", "RTE", "PET", "USA", "NYC"
  → se mantienen en mayúsculas

**Regla 2 — Eliminar prefijos de marca**
Eliminar el nombre del fabricante/marca cuando está al inicio
seguido de " - " o " – ":
- "BRIDOR - Danish Apple Butter" → "Danish Apple Butter"
- "BINDI - Cake Carrot" → "Cake Carrot"
- "MONIN - Caramel Syrup 750 ml" → "Caramel Syrup 750 ml"
- "REV. TEA - Bombay Chai" → "Bombay Chai"
- EXCEPCIÓN: si el nombre de la marca es parte esencial del
  nombre canónico (ej. "Aiya Matcha"), NO eliminar.
  Lista de excepciones: ["Aiya", "Pregel", "Lotus", "Califia"]

**Regla 3 — Eliminar especificaciones de tamaño del nombre**
Eliminar sufijos como "(3.52 oz.)", "750 ml", "x 12", "- 33.8 oz"
cuando van entre paréntesis o precedidos de " - ":
- "Tiramisu Cup (3.52 oz.)" → "Tiramisu Cup"
- "Caramel Syrup 750 ml" → "Caramel Syrup"
- "Cinnamon Roll Brioche RTB" → "Cinnamon Roll Brioche"
  (RTB = Ready to Bake, NO eliminar — es parte del nombre)

**Regla 4 — Caracteres especiales**
- Eliminar comillas dobles dentro del nombre
- Reemplazar "–" (em dash) por "-"
- Eliminar dobles espacios → un espacio
- Strip de espacios al inicio y final

**Regla 5 — Inglés**
No hay detección automática de idioma — solo aplicar las reglas
anteriores. Si el nombre ya está en inglés correcto, no cambia.

### Reglas de normalización de purchase_unit

**Regla 1 — Unidades de volumen**
Normalizar variantes al formato estándar:
- "750 mL", "750ml", "750 ML" → "750 ml"
- "1 Lt", "1 L", "1l", "1L" → "1 L"
- "64 oz", "64oz", "64 Oz" → "64 oz"
- "32 oz", "32 OZ" → "32 oz"

**Regla 2 — Unidades de peso**
- "3 LB", "3lb", "3 Lb" → "3 lb"
- "1 KG", "1kg" → "1 kg"
- "5 lbs" → "5 lb" (sin 's')

**Regla 3 — Conteos de empaque**
- "x 50", "X 50", "x50", "X50" → "× 50"
- "x 12 bags" → "× 12 bags"
- "6/4ct / 11 oz" → dejar como está (formato de empaque
  compuesto, no normalizar)

**Regla 4 — Case/pack**
- "case", "Case", "CASE", "cases", "Cases" → "case"
- "piece", "pieces", "Piece" → "piece"
- "ea", "each", "Each" → "each"
- "Bottle", "bottle" → "bottle"
- "Box", "box" → "box"

**Regla 5 — Strip y espacios**
- Strip de espacios al inicio y final
- Eliminar dobles espacios

### Output del script de normalización

Dos archivos:
1. `data/exports/ingredients_normalized_YYYY-MM-DD.csv`
   — el CSV completo con los valores normalizados aplicados
2. `data/exports/ingredients_changes_YYYY-MM-DD.csv`
   — solo las filas donde algo cambió, con columnas adicionales:
   `name_original`, `name_normalized`, `unit_original`, `unit_normalized`

El usuario revisa `ingredients_changes_YYYY-MM-DD.csv` para
validar que las normalizaciones son correctas antes de cargar.

### Criterio de aceptación
- Las reglas se aplican en el orden documentado
- El archivo de cambios muestra claramente qué cambió
- Filas sin cambios NO aparecen en el archivo de cambios
- Los números (conversion_factor, etc.) no se modifican

---

## Paso 3 — El usuario trabaja en Excel

El usuario:
1. Abre `ingredients_normalized_YYYY-MM-DD.csv` en Excel
2. Corrige conversion_factor para los ingredientes que necesita
3. Corrige current_price para los que necesita actualizar
4. Puede corregir nombres manualmente si la normalización
   automática no fue correcta
5. Guarda como CSV (mismo nombre o nombre nuevo)

**Columnas que el usuario puede modificar:**
- `name`
- `purchase_price`
- `purchase_unit`
- `usage_unit`
- `conversion_factor`
- `yield_percentage` (como porcentaje, ej. 98.0)
- `current_price`

**Columnas que NO debe modificar:**
- `id` — es la clave de carga, nunca cambiar
- `category` — se ignora en la carga
- `status` — se ignora en la carga

---

## Paso 4 — Script de carga (bulk import)

### Archivo a crear
```
backend/scripts/import_ingredients.py
```

### Comando de ejecución
```bash
# Dry run (muestra qué cambiaría sin tocar la BD):
python -m backend.scripts.import_ingredients \
  data/exports/ingredients_normalized_2026-07-10.csv --dry-run

# Carga real:
python -m backend.scripts.import_ingredients \
  data/exports/ingredients_normalized_2026-07-10.csv
```

### Lógica de carga

Para cada fila del CSV, comparar contra el valor actual en BD.
Solo actualizar los campos que cambiaron.

**Para `current_price`** (si cambió o si es diferente al actual):
```python
# NUNCA UPDATE directo — siempre via history
INSERT INTO ingredient_price_history
  (ingredient_id, price, source)
VALUES
  (:id, :new_price, 'bulk_import')
-- El trigger trg_iph_sync_current_price actualiza current_price
```

**Para todos los demás campos** (name, purchase_price,
purchase_unit, usage_unit, conversion_factor, yield_percentage):
```python
UPDATE ingredients SET
  name = :name,
  purchase_price = :purchase_price,
  purchase_unit = :purchase_unit,
  usage_unit = :usage_unit,
  conversion_factor = :conversion_factor,
  yield_percentage = :yield_percentage / 100,  -- CSV viene como %
  updated_at = now()
WHERE id = :id
  AND is_active = true  -- nunca tocar inactivos
```

**Validaciones antes de aplicar cada fila:**
- `id` existe en la BD → si no existe, skip con warning
- `name` no está vacío
- `conversion_factor` > 0
- `yield_percentage` entre 0.1 y 100 (como %)
- `current_price` > 0 si se especifica
- `name` no duplica otro ingrediente activo con distinto id

### Output del script de carga

Log detallado en consola y en archivo
`data/exports/import_log_YYYY-MM-DD-HHMMSS.txt`:

```
=== BULK IMPORT — 2026-07-10 ===
Total rows in CSV: 325
Rows processed: 320
Rows skipped (inactive): 5

Changes applied:
  - name updated: 47
  - purchase_price updated: 23
  - purchase_unit updated: 31
  - usage_unit updated: 8
  - conversion_factor updated: 15
  - yield_percentage updated: 3
  - current_price updated (via history): 28

Warnings:
  - Row 45 (id=99): name 'Chocolate Chip Cookie' already exists for id=151 → SKIPPED
  - Row 12 (id=999): id not found in DB → SKIPPED

Errors: 0
```

### Criterio de aceptación del script de carga
1. `--dry-run` muestra exactamente qué cambiaría sin tocar la BD
2. `current_price` se actualiza SIEMPRE via ingredient_price_history
3. Solo se actualiza lo que cambió (no hace UPDATE de todo)
4. Ingredientes inactivos nunca se tocan
5. El log muestra exactamente qué se hizo y qué se saltó
6. Si hay un error en una fila, continúa con las demás (no falla todo)
7. El script es idempotente — correrlo dos veces con el mismo CSV
   produce el mismo resultado

---

## Orden de implementación para Claude Code

```
1. Script export (Paso 1)
   → Verificar: correr y confirmar que el CSV tiene todos los campos

2. Script normalize (Paso 2)
   → Verificar: correr sobre el CSV exportado y revisar
     ingredients_changes_*.csv

3. Script import (Paso 4)
   → Verificar primero con --dry-run
   → Luego con carga real sobre datos de prueba
```

**El Paso 3 (trabajo del usuario en Excel) ocurre entre
los scripts 2 y 4 — Claude Code no lo implementa.**

---

## Reglas que NO cambian

- current_price SIEMPRE via ingredient_price_history
- Ingredientes inactivos nunca se modifican
- El id es la única clave de matching — nunca por nombre
- Log inmutable: el import_log es append-only, no se sobreescribe
- Sin inventar datos: si un campo en el CSV está vacío,
  se mantiene el valor actual en BD (no se pone NULL)
