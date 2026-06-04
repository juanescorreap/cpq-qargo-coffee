# Revisión de arquitectura PostgreSQL — Qargo Coffee

Análisis crítico del esquema de inventario, recetas, costos, proveedores y tiendas, con propuesta de refactorización orientada a robustez e integridad referencial, y a escalar a millones de registros sin cuellos de botella.

Entregables que acompañan este documento:

- `qargo_erd.mermaid` — diagrama entidad-relación del esquema objetivo, agrupado por dominios.
- `schema_refactorizado.sql` — DDL completo refactorizado, **validado ejecutándolo de principio a fin contra PostgreSQL 16** (no solo parseado): 37 tablas lógicas + particiones, 91 claves foráneas, 65 constraints UNIQUE, 39 CHECK, 1 EXCLUDE, 119 índices, 23 triggers y 4 vistas, todo dentro de una sola transacción que cierra en `COMMIT`.

---

## 0. Contexto y advertencias antes de leer

Tres aclaraciones que condicionan todo el análisis:

**El volcado de origen viene de una herramienta de introspección que pierde modificadores de tipo.** Por eso en `schema_completo.txt` aparece `character varying` sin longitud, `numeric` sin `(precision, scale)` y `character` sin `(n)`. Eso no significa que el diseño original no tuviera esos modificadores; significa que el volcado no es fiable como fuente de verdad sobre tipos. En el refactor los re-establezco de forma explícita, pero conviene contrastarlos contra los modelos reales de SQLModel/Alembic antes de migrar.

**La base está gestionada por Alembic** (la tabla `alembic_version` lo confirma). Por lo tanto el DDL refactorizado es un **estado objetivo (greenfield)**, no un script para ejecutar sobre producción. Sirve como especificación de "a dónde queremos llegar". Cada cambio debe traducirse a migraciones incrementales de Alembic, y varios de ellos (añadir `NOT NULL`, `CHECK`, `UNIQUE`, acciones de FK) exigen **backfill y de-duplicación de datos primero**, o la migración fallará al validar las filas existentes. La sección 5 detalla el orden recomendado.

**El esquema tiene dos generaciones claramente distinguibles.** Las tablas "viejas" del catálogo (`ingredients`, `products`, `stores`, `recipe_*`, `product_*`, `modifier*`, `competitor*`) son **laxas**: casi sin `NOT NULL`, sin `CHECK`, con booleanos anulables y sin `updated_at`. Las tablas "nuevas" de cadena de suministro (`supply_routes`, `supply_route_prices`, `ingredient_availability`, `recipe_cost_snapshots`, etc.) están **bien endurecidas**: `NOT NULL`, `CHECK` de dominio, `DEFAULT`, `metadata jsonb`, timestamps con zona. Gran parte del trabajo de refactor consiste en **subir el listón de las tablas viejas al nivel de las nuevas**.

---

## 1. Visualización del esquema (ERD)

El diagrama está en `qargo_erd.mermaid`. Mermaid `erDiagram` no soporta subgrafos visuales, así que los dominios se delimitan con banners de comentario dentro del archivo y se listan aquí. El modelo se organiza en siete dominios lógicos:

**Referencia / catálogos base** — `currencies` (nueva, ver 2.4), `categories`, `regions`, `recipe_units`, `ingredients`, `manufacturers`, `distributors`, `competitors`, `modifiers`. Son las dimensiones sin dependencias salientes.

**Tiendas y geografía** — `stores`, que apunta a `regions` y a `currencies`.

**Catálogo de productos y recetas** — `products`, `product_sizes`, `recipe_ingredients`, `recipe_sub_recipes` (lista de materiales auto-referencial), `size_packaging`, `category_margins`, `modifiers`, `product_modifier_costs`, `modifier_ingredient_effects`.

**Extensiones del catálogo de ingredientes** — `ingredient_recipe_unit_conversions`, `ingredient_substitutes` y la nueva tabla puente `ingredient_substitute_regions` (ver 2.3).

**Cadena de suministro** — `supply_routes`, `supply_route_assignments`, `ingredient_supplier_refs`, `supplier_unit_conversions`, `supply_route_prices`, `ingredient_availability`, `store_supplier_history`.

**Precios, costos e historial** — `store_ingredient_prices`, `product_pricing`, `product_price_history`, `ingredient_price_history`, `recipe_cost_snapshots`.

**Inteligencia competitiva** — `competitor_products`, `product_competitor_matches`.

Más el cruce `store_products` entre tiendas y productos, y `alembic_version` (gestionada externamente, fuera del modelo de negocio).

---

## 2. Análisis crítico y detección de errores

### 2.1. El problema transversal número uno: ningún índice sobre claves foráneas

PostgreSQL indexa automáticamente las PK y las restricciones UNIQUE, **pero no las claves foráneas**. El esquema original tiene del orden de 50 FKs y **ni un solo índice** que las cubra. Consecuencias directas a escala:

- Cada `JOIN` por la columna FK degenera en *sequential scan* del lado hijo.
- Cada `DELETE` o `UPDATE` sobre la tabla padre tiene que escanear la tabla hija completa para verificar la integridad referencial. Borrar un `ingredient` con 2M de filas en `ingredient_price_history` sin índice es un *seq scan* de 2M filas por cada borrado.

Es, con diferencia, el mayor cuello de botella latente. El refactor añade un índice B-Tree por cada FK, más índices compuestos alineados con los patrones de acceso reales (sección 4.1).

### 2.2. Restricciones faltantes (`NOT NULL`, `CHECK`, `UNIQUE`)

**Booleanos anulables con semántica de bandera.** `is_active`, `is_default`, `is_available`, `scales_with_size`, `is_sub_recipe` se declaran `boolean` sin `NOT NULL` ni `DEFAULT`. Un booleano de tres estados (`true`/`false`/`NULL`) es casi siempre un error: obliga a escribir `WHERE is_active IS NOT FALSE` y dispersa la lógica. Todos pasan a `NOT NULL DEFAULT true/false`.

**Faltan UNIQUE en tablas de cruce y de detalle.** Sin ellos, nada impide duplicados lógicos:

| Tabla | UNIQUE que faltaba | Qué evita |
|---|---|---|
| `recipe_ingredients` | `(product_id, ingredient_id)` | el mismo ingrediente dos veces en una receta |
| `recipe_sub_recipes` | `(parent_product_id, sub_recipe_id)` | sub-receta duplicada |
| `store_products` | `(store_id, product_id)` | producto repetido en una tienda |
| `store_ingredient_prices` | `(store_id, ingredient_id)` | dos precios locales "actuales" |
| `product_sizes` | `(product_id, size_name)` | dos tallas "Grande" |
| `size_packaging` | `(size_id, packaging_ingredient_id)` | empaque duplicado |
| `product_modifier_costs` | `(product_id, modifier_id)` | costo de modificador duplicado |
| `modifier_ingredient_effects` | `(modifier_id, ingredient_id)` | efecto duplicado |
| `product_competitor_matches` | `(our_product_id, our_size_id, competitor_product_id)` | match repetido |
| `ingredient_recipe_unit_conversions` | `(ingredient_id, recipe_unit_id)` | conversión duplicada |
| `ingredient_substitutes` | `(original_ingredient_id, substitute_ingredient_id)` | sustituto duplicado |

**`product_sizes.is_default` sin garantía de unicidad.** Nada impide marcar dos tallas como predeterminadas para el mismo producto. Se resuelve con un índice único parcial: `CREATE UNIQUE INDEX ... ON product_sizes (product_id) WHERE is_default`.

**Ausencia de validación de rango.** `yield_percentage`, `process_yield_loss`, `markup_percentage`, `quantity`, `conversion_factor`, precios… ninguno tiene `CHECK`. El refactor añade, vía dominios y CHECKs por columna: cantidades y precios `>= 0`, porcentajes en `[0,100]`, factores de conversión `> 0`, prioridades `>= 1`.

### 2.3. Riesgos de integridad referencial

**`ingredient_substitutes.affects_regions ARRAY` rompe la integridad referencial.** Un array de IDs de región es un conjunto de claves foráneas que el motor **no puede verificar**: nada impide meter un `region_id` que no existe, y borrar una región deja IDs colgados dentro del array. Es una FK implícita sin enforcement. La solución relacional es la tabla puente `ingredient_substitute_regions (substitute_id, region_id)` con FKs reales en ambas columnas y `ON DELETE CASCADE`. Convención: conjunto vacío = aplica globalmente.

**`recipe_sub_recipes` sin protección contra auto-referencia ni ciclos.** Al ser una lista de materiales auto-referencial (`products` → `products`), el esquema original permite que un producto sea su propia sub-receta (`parent_product_id = sub_recipe_id`), lo que produce recursión infinita al calcular costos. Añado `CHECK (parent_product_id <> sub_recipe_id)` para el caso directo. **La detección de ciclos profundos (A→B→A) no se puede expresar con un CHECK** y debe resolverse en la capa de aplicación o con un trigger recursivo sobre un `WITH RECURSIVE`; lo dejo documentado en el SQL como límite explícito.

**Ninguna FK declara `ON DELETE` / `ON UPDATE`.** Todas usan el `NO ACTION` implícito. Eso no es seguro, es simplemente *no decidido*: el comportamiento ante borrados queda indefinido y se descubre en producción. El refactor adopta una política explícita y uniforme:

- `CASCADE` cuando la fila hija no tiene sentido sin el padre: líneas de receta, tallas, cruces, precios de ruta.
- `RESTRICT` para bloquear el borrado de una dimensión que todavía se referencia como hecho de negocio: un `ingredient` usado en recetas, un `product` que es objeto de auditoría. Estas dimensiones usan borrado lógico (`is_active`) de todos modos.
- `SET NULL` para referencias opcionales: `region`, fuente de modificador, tienda en asignaciones de ruta.

### 2.4. Tipos de datos inapropiados

**`numeric` sin precisión en todo lo monetario y de cantidades.** `numeric` sin `(p,s)` en Postgres es de precisión arbitraria: correcto en resultado pero más pesado en almacenamiento e índices, y sin contrato de redondeo. Para dinero es además un riesgo de consistencia. El refactor introduce **dominios** como única fuente de verdad:

```sql
CREATE DOMAIN price_amount    AS numeric(14, 4) CHECK (VALUE >= 0);
CREATE DOMAIN quantity_amount AS numeric(14, 6) CHECK (VALUE >= 0);
CREATE DOMAIN pct_amount      AS numeric(6, 3);
CREATE DOMAIN iso_country     AS char(2) CHECK (VALUE ~ '^[A-Z]{2}$');
```

Cuatro decimales en precio porque, aunque el COP no tiene unidad menor, USD/EUR sí, y conviene cubrir conversiones intermedias. Seis decimales en cantidades para la aritmética de unidades (gramos por onza, etc.). **Una excepción importante:** `modifier_ingredient_effects.quantity_change` y `product_modifier_costs.cost_impact` representan cambios **con signo** (un modificador puede *quitar* ingrediente), así que no pueden usar los dominios no-negativos; quedan como `numeric(14,6)`/`numeric(14,4)` con signo, y a `quantity_change` le añado `CHECK (quantity_change <> 0)` porque un efecto de cero no aporta nada.

**`character varying` sin longitud == `text`.** En Postgres `varchar` sin `n` es funcionalmente idéntico a `text`; tenerlo como "varchar" solo confunde sin aportar validación. Criterio aplicado: longitud acotada y semántica (`varchar(160)` para nombres, `varchar(40)` para códigos) donde el valor tiene un límite natural de negocio, y `text` honesto para notas y URLs libres.

**`currency_code` inconsistente y `country_code` como `character` (sin `n`).** El esquema mezcla `currency_code character varying DEFAULT 'COP'` en unas tablas y `currency_code character CHECK (~ '^[A-Z]{3}$')` en otras (donde `character` sin longitud es `char(1)`, ¡que ni siquiera admite tres letras!). Lo mismo con `country_code character` → `char(1)` con default `'CO'` que se trunca. Dos correcciones:

- Se crea la tabla de referencia **`currencies`** (ISO 4217, semilla COP/USD/EUR) y todas las columnas monetarias pasan a `char(3)` con FK real a `currencies(code)` y `ON UPDATE CASCADE`. Esto elimina la inconsistencia y da un objetivo de FK verificable.
- `country_code` pasa al dominio `iso_country` (`char(2)` + regex de mayúsculas).

### 2.5. Auditoría incompleta

Muchas tablas viejas no tienen `updated_at` (p. ej. `product_sizes` y `recipe_ingredients` lo tienen pero anulable; `size_packaging`, `category_margins`, `store_products`, `product_pricing` directamente no). Y donde existe, **nada lo mantiene**: se rellena en el `INSERT` por `DEFAULT now()` pero un `UPDATE` no lo toca. El refactor: (1) añade `created_at`/`updated_at timestamptz NOT NULL DEFAULT now()` a toda tabla mutable; (2) crea la función `set_updated_at()` y la engancha con un trigger `BEFORE UPDATE` en las 23 tablas mutables mediante un bloque `DO`. Las columnas `*_by` (`created_by`, `changed_by`, `assigned_by`…) hoy son texto libre; lo dejo así por ahora, pero la nota de futuro es modelarlas como FK a una tabla `users` cuando exista.

---

## 3. Optimización arquitectónica: tablas vs. vistas

La pregunta clave es **dato derivado (calcular) vs. hecho histórico (persistir)**. Veredicto tabla por tabla:

**`ingredient_price_history` y `product_price_history` → se quedan como tablas.** Son hechos de auditoría *append-only*: registran "este precio rigió en este momento". No son derivables (el precio de ayer no se recalcula desde el estado de hoy) y se necesitan para análisis histórico y cumplimiento. Persistencia plenamente justificada. Lo que **sí** sobra es consultarlas con `ORDER BY changed_at DESC LIMIT 1` para obtener "el precio actual": eso lo resuelve la vista `v_current_ingredient_price` con `DISTINCT ON (ingredient_id)`.

**`recipe_cost_snapshots` → se queda como tabla.** Es el caso de libro de un *snapshot* inmutable: congela el costo de una receta en un instante, con su desglose en `snapshot_detail jsonb`. Recalcularlo "al vuelo" sería imposible porque los precios de los insumos ya cambiaron. Es escritura-una-vez / lectura-rara, perfecto para persistir y particionar.

**`product_modifier_costs` → debería ser una vista (o, a lo sumo, una caché).** Aquí está el dato derivado más claro: `cost_impact` se puede calcular como Σ(`modifier_ingredient_effects.quantity_change` × precio actual del ingrediente). Mantenerlo como tabla introduce el clásico riesgo de *staleness*: si cambia un precio o un efecto y nadie recalcula la fila, el costo queda desactualizado y en silencio. El refactor entrega la vista `v_product_modifier_cost` que lo recomputa en vivo. Dejo la tabla como caché opcional **solo** si hay una razón de rendimiento medida; en ausencia de ella, la vista debería ser la fuente de verdad.

**`product_pricing` → se queda como tabla, con matiz.** A diferencia del costo de modificadores, esta tabla guarda **decisiones humanas** que ninguna fórmula puede regenerar: `is_manual_price` y `markup_override` son overrides manuales. Eso la convierte en estado legítimo, no en dato derivable. Lo que añado es la vista `v_product_effective_price` para encapsular la regla "el precio manual gana sobre el calculado", de modo que la aplicación consuma una sola fuente coherente. Nota: el precio "actual" vive en `product_pricing` y el histórico en `product_price_history`; conviene un trigger que escriba en el historial en cada cambio de la primera.

**`ingredient_availability` → tabla, con vista de conveniencia.** Es estado con vigencia temporal (`valid_from`/`valid_until`). Se queda como tabla y añado `v_current_ingredient_availability` para los registros vigentes hoy.

**Sobre vistas materializadas:** ninguna es necesaria *de entrada*. La candidata natural sería un dashboard de "margen efectivo por producto/tienda" que cruce `product_pricing`, `recipe_cost_snapshots` y `category_margins`. Si esa consulta agregada se vuelve cara y se consulta seguido, conviene una `MATERIALIZED VIEW` con `REFRESH ... CONCURRENTLY` programado. Mientras tanto, vistas normales: corrección por encima de rendimiento prematuro.

**Posible redundancia a vigilar:** `store_supplier_history` y `supply_route_assignments` solapan parcialmente (ambas relacionan tienda/ruta con vigencia). No las fusiono porque cumplen roles distintos —asignación operativa vigente vs. bitácora histórica de cambios— pero conviene confirmar que no se esté escribiendo la misma información en las dos.

---

## 4. Estrategia de escalabilidad a largo plazo

### 4.1. Índices

Además del índice por cada FK (2.1), índices **compuestos** alineados con los accesos reales, varios con orden `DESC` en la fecha para servir consultas "lo más reciente" sin ordenamiento:

- `product_price_history (product_id, size_id, changed_at DESC)`
- `ingredient_price_history (ingredient_id, changed_at DESC)`
- `recipe_cost_snapshots (product_id, store_id, calculated_at DESC)`
- `supply_route_prices (supply_route_id, valid_from DESC)`
- `store_supplier_history (store_id, ingredient_id, valid_from DESC)`
- `competitor_products (competitor_id, scraped_at DESC)`

**Índices únicos parciales** para reglas que un UNIQUE normal no expresa: `uq_product_sizes_default` (una sola talla default por producto, `WHERE is_default`), `uq_product_pricing_current` con `COALESCE(store_id, 0)` para que la fila de precio "global" (store_id NULL) también sea única, y `uq_isr_external_code` para SKUs externos solo donde existen.

**GIN para JSONB** — disponible pero **comentado**, para activarse solo cuando exista el patrón de consulta que lo justifique. No tiene sentido pagar el costo de mantenimiento de un GIN sobre `metadata`/`snapshot_detail` si nunca se filtra por su contenido. Igual para los índices `gin_trgm_ops` de búsqueda difusa por nombre.

### 4.2. Particionamiento por fecha

Cuatro tablas son *append-mostly* y crecen sin techo. Se definen **particionadas por rango desde el inicio**, porque convertir una tabla grande a particionada después es una migración cara:

- `ingredient_price_history` → `RANGE (changed_at)`
- `product_price_history` → `RANGE (changed_at)`
- `recipe_cost_snapshots` → `RANGE (calculated_at)`
- `competitor_products` → `RANGE (scraped_at)` (el log de scraping crece muy rápido)

Detalle técnico clave: **la PK de una tabla particionada debe incluir la clave de partición**, por eso la PK pasa a ser compuesta (`(id, changed_at)`, `(id, calculated_at)`, `(id, scraped_at)`). Esto tiene un efecto en cascada: `product_competitor_matches` referencia a `competitor_products`, y una FK a una tabla particionada debe apuntar a su PK completa; por eso `product_competitor_matches` lleva la columna extra `competitor_product_scraped_at` como parte de la FK compuesta. Validé que el enrutamiento funciona: filas de 2025, 2026 y una de 2030 caen correctamente en sus particiones (`_2025`, `_2026`, `_default`).

Cada tabla incluye particiones `_2025`, `_2026` y una `DEFAULT`. **Para producción, automatizar la creación de particiones futuras con `pg_partman`** en lugar de crearlas a mano; la partición `DEFAULT` es solo una red de seguridad, no un plan de capacidad.

### 4.3. Timestamps y zonas horarias

Donde existen, los timestamps ya usan `timestamp with time zone` (`timestamptz`), que es lo correcto: cadena multi-tienda y posible multi-país. El refactor estandariza esto en **todas** las tablas y, sobre todo, cierra el hueco de mantenimiento de `updated_at` con el trigger de 4.x. Fechas de vigencia de negocio (`valid_from`, `valid_until`, `effective_date`, `approval_date`) se quedan como `date`: representan días calendario, no instantes, y no deben llevar zona.

### 4.4. Vigencias temporales sin protección contra solapamiento

Varias tablas tienen ventanas `valid_from`/`valid_until` pero **nada garantiza su coherencia**: ni `valid_until >= valid_from`, ni la no-superposición de ventanas. Dos refuerzos:

- `CHECK (valid_until IS NULL OR valid_until >= valid_from)` en `supply_route_prices`, `supply_route_assignments`, `ingredient_availability`, `ingredient_substitutes`, `store_supplier_history`.
- Para precios de ruta, donde dos ventanas solapadas para la misma ruta son un error de datos grave (¿qué precio rige?), una **restricción de exclusión** —imposible de expresar con CHECK— usando `btree_gist`:

```sql
CONSTRAINT no_overlap_srp EXCLUDE USING gist (
  supply_route_id WITH =,
  daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
)
```

Lo verifiqué en la instancia real: insertar una segunda ventana que solapa con otra existente para la misma ruta es rechazado con `exclusion_violation`.

### 4.5. JSONB: ¿justificado o falta de normalización?

Los tres usos actuales de `jsonb` están razonablemente justificados, pero con matices:

- `manufacturers.metadata`, `distributors.metadata`, `supply_routes.metadata`, `regions.metadata` → bolsas de atributos heterogéneos y opcionales. Aceptable como *escape hatch*. **Recomendación:** auditar periódicamente su contenido y *promover a columna* cualquier clave que se consulte o filtre con frecuencia (si todos los proveedores acaban teniendo `metadata->>'payment_terms'`, eso quiere ser una columna).
- `recipe_cost_snapshots.snapshot_detail` → es el uso **ideal** de JSONB: un documento de desglose inmutable, heterogéneo, escritura-una-vez. Aquí normalizar sería contraproducente.

Ninguno esconde una falta de normalización grave hoy. El único caso que **sí** era falta de normalización disfrazada —`ingredient_substitutes.affects_regions ARRAY`— ya se corrigió con la tabla puente (2.3). La regla para el futuro: JSONB para lo verdaderamente variable y no consultado por sus campos; tabla para todo lo que tenga integridad referencial o se filtre.

### 4.6. Claves e identidad

El volcado usa `integer ... nextval(...seq)` (es decir, `serial`). El refactor migra a `bigint GENERATED ALWAYS AS IDENTITY`: `bigint` para no toparse con el techo de `int4` (~2.100 millones) en las tablas de alto crecimiento —los históricos llegan ahí—, e `IDENTITY` (estándar SQL) en vez de `serial` porque gestiona mejor la propiedad de la secuencia y no permite inserciones accidentales en la columna de identidad.

---

## 5. Resumen del refactor y nota de migración

El archivo `schema_refactorizado.sql` aplica todo lo anterior, con comentarios SQL en cada decisión no obvia y en **orden de creación correcto para las FKs**: extensiones → dominios → función de trigger → tablas de referencia → tiendas → catálogo → extensiones de ingrediente → cadena de suministro → precios/historial → inteligencia competitiva → cruces → índices → triggers → vistas, todo en una transacción.

Cambios estructurales respecto al original:

- **Tabla nueva `currencies`** como destino de FK para todo lo monetario.
- **Tabla puente nueva `ingredient_substitute_regions`** que reemplaza la columna `affects_regions ARRAY`.
- **Cuatro tablas particionadas** por fecha con PK compuesta.
- **Cuatro vistas** (`v_current_ingredient_price`, `v_product_modifier_cost`, `v_current_ingredient_availability`, `v_product_effective_price`).
- **Cuatro dominios**, una función de trigger y 23 triggers de `updated_at`.

### Nota de migración con Alembic (importante)

Este DDL **no se ejecuta sobre la base viva**. La ruta segura, por fases:

1. **Aditivo y sin riesgo primero:** crear `currencies` (con semilla), crear `ingredient_substitute_regions`, añadir columnas `created_at`/`updated_at` faltantes con `DEFAULT now()`, crear la función `set_updated_at` y sus triggers, y **crear todos los índices con `CREATE INDEX CONCURRENTLY`** (fuera de transacción, para no bloquear escrituras). Nada de esto rompe datos existentes.
2. **Backfill antes de restringir:** poblar `ingredient_substitute_regions` desde los arrays actuales; normalizar `currency_code`/`country_code` a mayúsculas y a la longitud correcta; de-duplicar las filas que violarían los nuevos UNIQUE; corregir cualquier `valid_until < valid_from` y cualquier solapamiento de ventanas de precio.
3. **Endurecer al final, ya con datos limpios:** aplicar `NOT NULL`, `CHECK`, `UNIQUE`, la restricción `EXCLUDE` y las acciones `ON DELETE`/`ON UPDATE` de las FKs. Cada `ALTER` aquí valida las filas existentes y fallará si el paso 2 quedó incompleto, lo cual es la red de seguridad deseada.
4. **Migrar tipos y particiones por separado:** el cambio `serial → bigint IDENTITY` y la conversión de los históricos a tablas particionadas son las migraciones más pesadas (reescriben datos). Conviene hacerlas en ventanas de mantenimiento dedicadas, idealmente con la estrategia de tabla-sombra + intercambio para minimizar bloqueo.

La detección de ciclos en `recipe_sub_recipes` (más allá del auto-referencia directo) se implementa en la capa de aplicación o con un trigger recursivo; no es expresable como constraint declarativa.
