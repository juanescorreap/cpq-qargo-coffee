# Qargo Coffee — Expansión de arquitectura de datos: cadena de suministro regional

> **Documento para Claude Code.** Implementación paso a paso del modelo de proveedores, fabricantes, rutas de suministro, precios, disponibilidad regional y sustitutos de ingredientes. Lee este documento completo antes de ejecutar cualquier migración.

---

## Índice

1. [Contexto del negocio](#1-contexto-del-negocio)
2. [Principios rectores](#2-principios-rectores)
3. [Estado actual del schema](#3-estado-actual-del-schema)
4. [Mapa de la expansión](#4-mapa-de-la-expansión)
5. [Fase 1 — Geografía, fabricantes y distribuidores](#5-fase-1--geografía-fabricantes-y-distribuidores)
6. [Fase 2 — Rutas de suministro](#6-fase-2--rutas-de-suministro)
7. [Fase 3 — Referencias externas, unidades y precios](#7-fase-3--referencias-externas-unidades-y-precios)
8. [Fase 4 — Disponibilidad regional y sustitutos](#8-fase-4--disponibilidad-regional-y-sustitutos)
9. [Fase 5 — Historial de relaciones y snapshots de costo](#9-fase-5--historial-de-relaciones-y-snapshots-de-costo)
10. [Fase 6 — Modificaciones al schema existente](#10-fase-6--modificaciones-al-schema-existente)
11. [Función de resolución de ruta](#11-función-de-resolución-de-ruta)
12. [Índices y performance](#12-índices-y-performance)
13. [Verificación post-implementación](#13-verificación-post-implementación)
14. [Bugs que este modelo previene](#14-bugs-que-este-modelo-previene)
15. [Lo que queda intencionalmente sin modelar](#15-lo-que-queda-intencionalmente-sin-modelar)

---

## 1. Contexto del negocio

Qargo Coffee es una franquicia de café con ~17 tiendas en Colombia. El schema actual asume que todos los ingredientes se compran igual en todas las tiendas. La realidad es:

- Hay fabricantes con distribuidores diferentes por región.
- Algunas tiendas compran directamente a fabricantes distintos.
- El mismo ingrediente tiene nombres y códigos diferentes según el distribuidor o fabricante.
- Los precios varían por ubicación geográfica y en el tiempo.
- Algunos ingredientes no están disponibles en ciertas regiones.
- Los IDs de productos no son consistentes entre distribuidores.
- Algunas ubicaciones tienen más de una opción de proveedor disponible.
- Corporativo define una ruta primaria y una alternativa por región, pero una tienda puede tener excepción puntual.
- Cuando un ingrediente no está disponible, **corporativo** define el sustituto aceptado.
- Nunca hay dos proveedores activos simultáneamente para el mismo ingrediente en la misma tienda — la alternativa solo entra si la primaria falla.

---

## 2. Principios rectores

Estas reglas gobiernan cada decisión de diseño. Ante cualquier duda de implementación, vuelve aquí.

### P1 — Separación canónico / externo

Qargo define sus propias entidades internas: ingredientes, unidades de receta, categorías. Todo lo que viene del mundo exterior (nombres de proveedores, códigos de catálogo, unidades de empaque) vive en tablas separadas vinculadas al canónico. **Nunca al revés.** El ingrediente `leche_entera` existe en la tabla `ingredients` una sola vez. Que el Distribuidor Norte lo llame "Leche Entera Pasteurizada 3.5%" y el Distribuidor Sur lo llame "Leche Fresca Bolsa" es un dato de referencia externa, no del canónico.

### P2 — Vigencia temporal en cualquier relación que cambie

Toda relación que pueda cambiar en el tiempo lleva `valid_from DATE NOT NULL` y `valid_until DATE` (nullable). El estado actual es **siempre** la fila donde `valid_until IS NULL`. Esto hace que el historial sea una consecuencia natural del modelo, no una tabla de auditoría separada. **Nunca se hace UPDATE de datos de negocio en filas vigentes** — se cierra la fila con `valid_until = hoy` y se inserta una nueva.

### P3 — Precio en moneda explícita, siempre

Ningún campo de precio existe sin su `currency_code CHAR(3)` (ISO 4217: `COP`, `USD`, `EUR`). Precios sin moneda son datos corruptos latentes. Agregar moneda después es una migración dolorosa. Agregarla desde el inicio no cuesta nada.

### P4 — Unicidad como contrato de datos

Cada tabla que represente "el estado vigente de X" tiene un constraint `UNIQUE` o `EXCLUDE` que lo garantiza a nivel de base de datos, **no solo en código de aplicación**. El código puede tener bugs. Los constraints de base de datos no.

### P5 — Extensibilidad sin migración disruptiva

Las tablas de configuración y criterios usan `metadata JSONB` para atributos futuros que hoy no son modelables (criterio de selección de proveedor preferido, scoring de confiabilidad, tier de contrato). Cuando esos conceptos maduren, se agregan columnas o tablas sobre una base limpia. No se rompe lo existente.

### P6 — Una sola fuente de verdad para lógica de negocio compleja

La lógica "¿qué ruta usa esta tienda para este ingrediente hoy?" vive en una función de Postgres (`fn_resolve_supply_route`), no en el ORM, no en el ETL, no en el pricing engine por separado. Cualquier capa que necesite esa respuesta llama la función. Si la regla cambia, se cambia en un solo lugar.

### P7 — Constraints que hacen imposible el estado incoherente

Un estado incoherente que el negocio no puede explicar (ruta sin fabricante ni distribuidor, sustituto de un ingrediente consigo mismo, dos rutas primarias vigentes para el mismo scope) debe ser imposible a nivel de base de datos, no solo "nunca debería pasar". Usamos `CHECK` y `EXCLUDE USING gist` para esto.

### P8 — Dos tablas en lugar de una cuando los conceptos son distintos

`supply_routes` define **qué existe** (la ruta fabricante→distribuidor para un ingrediente). `supply_route_assignments` define **quién usa qué y cuándo** (la asignación de esa ruta a una región o tienda). Mezclarlos en una tabla genera duplicación y hace que las excepciones por tienda sean complicadas de manejar limpiamente.

---

## 3. Estado actual del schema

Tablas existentes relevantes para esta expansión. **No modificar ninguna hasta la Fase 6.**

```
ingredients          — entidad canónica de ingredientes
stores               — tiendas individuales
products             — productos del menú
recipe_ingredients   — ingredientes por producto con cantidad y unidad
recipe_units         — unidades de medida para recetas
product_pricing      — precios calculados por producto/talla/tienda
product_price_history — historial de precios
store_ingredient_prices — precios locales actuales (a reemplazar parcialmente)
ingredient_price_history — historial de precios de ingredientes
ingredient_recipe_unit_conversions — conversiones de unidad (a extender)
```

### Problemas conocidos en el schema actual que esta expansión resuelve

| Problema | Ubicación actual | Solución en este plan |
|---|---|---|
| `category_margins` desconectada por `varchar` sin FK | `category_margins.category` vs `products.category` | Fase 6: tabla `categories` con FK real |
| `product_pricing` sin UNIQUE constraint | `product_pricing` | Fase 6: agregar UNIQUE |
| Precios sin moneda | `product_pricing`, `store_ingredient_prices` | Fase 3 y 6: agregar `currency_code` |
| Sin `updated_at` en tablas de receta | `recipe_ingredients`, `product_sizes` | Fase 6: agregar columna + trigger |
| `ingredient_recipe_unit_conversions` unidireccional, sin contexto de proveedor | `ingredient_recipe_unit_conversions` | Fase 3: nueva tabla `supplier_unit_conversions` |
| `store_ingredient_prices` sin vigencia temporal | `store_ingredient_prices` | Fase 3: reemplazado por `supply_route_prices` |

---

## 4. Mapa de la expansión

```
Fase 1  →  regions, manufacturers, distributors
Fase 2  →  supply_routes, supply_route_assignments
Fase 3  →  ingredient_supplier_refs, supplier_unit_conversions, supply_route_prices
Fase 4  →  ingredient_availability, ingredient_substitutes
Fase 5  →  store_supplier_history, recipe_cost_snapshots
Fase 6  →  modificaciones al schema existente
Post    →  fn_resolve_supply_route (función), índices, vistas
```

Cada fase es una migración de Alembic independiente. Las fases son acumulativas — no ejecutar una fase sin haber completado la anterior.

---

## 5. Fase 1 — Geografía, fabricantes y distribuidores

### Propósito

Crear las entidades base que no dependen de nada nuevo. Son el piso del modelo. `stores` adquirirá un `region_id` en la Fase 6.

### Migración

```sql
-- ─────────────────────────────────────────────────────────────────
-- TABLA: regions
-- Unidad geográfica de Qargo. Puede ser ciudad, zona o
-- departamento según como se organice el negocio.
-- Las tiendas se vinculan a una región en la Fase 6.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE public.regions (
    id           SERIAL          PRIMARY KEY,
    name         VARCHAR(100)    NOT NULL,
    code         VARCHAR(20)     NOT NULL UNIQUE,  -- 'BOG', 'MED', 'CTG', etc.
    country_code CHAR(2)         NOT NULL DEFAULT 'CO',
    is_active    BOOLEAN         NOT NULL DEFAULT true,
    metadata     JSONB,
    created_at   TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.regions IS
    'Unidades geográficas de operación de Qargo. Las tiendas pertenecen a una región '
    'y las rutas de suministro se definen a nivel regional como base.';

-- ─────────────────────────────────────────────────────────────────
-- TABLA: manufacturers
-- Empresas que fabrican físicamente el producto.
-- Un ingrediente canónico puede tener múltiples fabricantes.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE public.manufacturers (
    id           SERIAL          PRIMARY KEY,
    name         VARCHAR(200)    NOT NULL,
    country_code CHAR(2)         NOT NULL DEFAULT 'CO',
    tax_id       VARCHAR(50),                       -- NIT o equivalente
    website      TEXT,
    is_active    BOOLEAN         NOT NULL DEFAULT true,
    metadata     JSONB,          -- certificaciones, tier, etc. (extensible sin migración)
    created_at   TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.manufacturers IS
    'Fabricantes reales del producto físico. Entidad independiente del ingrediente canónico '
    'de Qargo — un fabricante puede proveer múltiples ingredientes.';

-- ─────────────────────────────────────────────────────────────────
-- TABLA: distributors
-- Intermediarios entre fabricante y tienda.
-- Un distribuidor puede cubrir múltiples regiones.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE public.distributors (
    id            SERIAL          PRIMARY KEY,
    name          VARCHAR(200)    NOT NULL,
    country_code  CHAR(2)         NOT NULL DEFAULT 'CO',
    tax_id        VARCHAR(50),
    contact_email VARCHAR(200),
    contact_phone VARCHAR(50),
    is_active     BOOLEAN         NOT NULL DEFAULT true,
    metadata      JSONB,          -- scoring, tier, condiciones comerciales generales
    created_at    TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.distributors IS
    'Distribuidores intermediarios. La cobertura geográfica real se determina '
    'por las rutas activas en supply_route_assignments, no por un campo de región aquí.';

-- ─────────────────────────────────────────────────────────────────
-- Triggers updated_at
-- ─────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.fn_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_regions_updated_at
    BEFORE UPDATE ON public.regions
    FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();

CREATE TRIGGER trg_manufacturers_updated_at
    BEFORE UPDATE ON public.manufacturers
    FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();

CREATE TRIGGER trg_distributors_updated_at
    BEFORE UPDATE ON public.distributors
    FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();
```

### Verificación de Fase 1

```sql
-- Deben existir las tres tablas
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('regions', 'manufacturers', 'distributors')
ORDER BY table_name;
-- Resultado esperado: 3 filas

-- Verificar que los triggers existen
SELECT trigger_name, event_object_table
FROM information_schema.triggers
WHERE trigger_schema = 'public'
  AND trigger_name LIKE 'trg_%_updated_at'
ORDER BY event_object_table;
-- Resultado esperado: 3 filas
```

---

## 6. Fase 2 — Rutas de suministro

### Propósito

Modelar **qué rutas existen** (`supply_routes`) y **quién las usa y cuándo** (`supply_route_assignments`). Esta es la tabla central de toda la expansión.

### Decisiones de diseño

**¿Por qué dos tablas?** `supply_routes` define el camino lógico fabricante→distribuidor para un ingrediente. `supply_route_assignments` asigna ese camino a una región o tienda con vigencia temporal y prioridad. Mezclarlos generaría duplicación: si tres regiones usan la misma ruta del mismo fabricante, habría tres filas idénticas salvo por el `region_id`.

**¿Por qué `priority INTEGER` en lugar de `is_primary BOOLEAN`?** Un booleano tiene el bug inherente de que dos filas pueden tener `is_primary = true` simultáneamente. Un `INTEGER` combinado con un `EXCLUDE` constraint hace eso imposible. Además, si en el futuro hay una tercera opción (primaria → alternativa → emergencia), `priority = 3` funciona sin migración.

**¿Por qué `EXCLUDE USING gist` en lugar de `UNIQUE`?**
`UNIQUE` no maneja rangos de fecha solapados. Dos filas con `valid_from = '2024-01-01'` y `valid_from = '2024-06-01'` son distintas en UNIQUE aunque el período `2024-01-01` a `2024-06-01` se solape con la segunda. `EXCLUDE USING gist` con `daterange` garantiza que no haya solapamiento de períodos para el mismo scope y prioridad.

### Prerequisito

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;
-- Necesario para EXCLUDE USING gist con tipos no-geométricos (integer, daterange)
-- Viene incluida en PostgreSQL, solo necesita activarse
```

### Migración

```sql
-- ─────────────────────────────────────────────────────────────────
-- TABLA: supply_routes
-- Define qué ruta existe: el camino abstracto
-- fabricante → (distribuidor) → ingrediente canónico.
-- No sabe nada de tiendas ni regiones.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE public.supply_routes (
    id               SERIAL      PRIMARY KEY,
    ingredient_id    INTEGER     NOT NULL
                                 REFERENCES public.ingredients(id),
    manufacturer_id  INTEGER
                                 REFERENCES public.manufacturers(id),
    distributor_id   INTEGER
                                 REFERENCES public.distributors(id),
    is_direct        BOOLEAN     NOT NULL DEFAULT false,
    -- is_direct = true: la tienda compra directamente al fabricante
    -- en ese caso distributor_id DEBE ser NULL
    is_active        BOOLEAN     NOT NULL DEFAULT true,
    metadata         JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Al menos un proveedor debe existir, salvo que sea compra directa
    -- En compra directa, el fabricante se registra en ingredient_supplier_refs
    CONSTRAINT supply_routes_source_check CHECK (
        is_direct = true
        OR manufacturer_id IS NOT NULL
        OR distributor_id  IS NOT NULL
    ),

    -- Compra directa no puede tener distribuidor (sería contradictorio)
    CONSTRAINT supply_routes_direct_no_distributor CHECK (
        NOT (is_direct = true AND distributor_id IS NOT NULL)
    )
);

COMMENT ON TABLE public.supply_routes IS
    'Definición abstracta de una ruta de suministro para un ingrediente canónico. '
    'No incluye a qué tienda o región aplica — eso está en supply_route_assignments.';

COMMENT ON COLUMN public.supply_routes.is_direct IS
    'Si true, la tienda compra directamente al fabricante sin distribuidor intermediario. '
    'En este caso distributor_id debe ser NULL.';

CREATE TRIGGER trg_supply_routes_updated_at
    BEFORE UPDATE ON public.supply_routes
    FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();

CREATE INDEX idx_supply_routes_ingredient_active
    ON public.supply_routes(ingredient_id)
    WHERE is_active = true;


-- ─────────────────────────────────────────────────────────────────
-- TABLA: supply_route_assignments
-- Asigna una ruta a una región o tienda específica.
-- Incluye prioridad (1=primaria, 2=alternativa) y vigencia temporal.
--
-- REGLA DE RESOLUCIÓN:
--   1. Si existe una asignación con store_id = X → usar esa (override de tienda)
--   2. Si no, usar la asignación de la región a la que pertenece la tienda
--   3. Dentro del mismo scope, menor priority gana
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE public.supply_route_assignments (
    id               SERIAL       PRIMARY KEY,
    supply_route_id  INTEGER      NOT NULL
                                  REFERENCES public.supply_routes(id),
    region_id        INTEGER
                                  REFERENCES public.regions(id),
    store_id         INTEGER
                                  REFERENCES public.stores(id),
    -- store_id NOT NULL  → override puntual de tienda (corporativo define excepción)
    -- store_id NULL      → asignación regional (aplica a todas las tiendas de la región)
    -- ambos NULL         → inválido (bloqueado por CHECK abajo)

    priority         INTEGER      NOT NULL DEFAULT 1,
    -- 1 = ruta primaria
    -- 2 = ruta alternativa (entra si la primaria falla o está en desabastecimiento)
    -- 3+ = niveles adicionales futuros

    valid_from       DATE         NOT NULL DEFAULT CURRENT_DATE,
    valid_until      DATE,
    -- valid_until NULL = asignación vigente actualmente
    -- Para cerrar: UPDATE SET valid_until = today, luego INSERT nueva fila

    change_reason    VARCHAR(200),
    -- Valores sugeridos: 'precio', 'desabastecimiento', 'calidad',
    --                    'logistica', 'contrato', 'nuevo_distribuidor'
    assigned_by      VARCHAR(100) NOT NULL,
    notes            TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),

    -- ── CONSTRAINTS DE INTEGRIDAD ─────────────────────────────────

    -- Al menos un scope debe estar definido
    CONSTRAINT sra_scope_required CHECK (
        region_id IS NOT NULL OR store_id IS NOT NULL
    ),

    -- Un override de tienda no lleva region_id
    -- (la región se deriva de stores.region_id, no se almacena aquí)
    CONSTRAINT sra_single_scope CHECK (
        NOT (region_id IS NOT NULL AND store_id IS NOT NULL)
    ),

    -- Prioridad debe ser positiva
    CONSTRAINT sra_priority_positive CHECK (priority >= 1),

    -- ── EXCLUSION CONSTRAINTS (previenen solapamiento de vigencias) ─

    -- Para asignaciones de tienda: no puede haber dos con la misma prioridad
    -- para el mismo scope en el mismo período
    EXCLUDE USING gist (
        store_id WITH =,
        priority WITH =,
        daterange(valid_from, COALESCE(valid_until, '9999-12-31'::date), '[)') WITH &&
    ) WHERE (store_id IS NOT NULL),

    -- Para asignaciones regionales: igual
    EXCLUDE USING gist (
        region_id WITH =,
        priority  WITH =,
        daterange(valid_from, COALESCE(valid_until, '9999-12-31'::date), '[)') WITH &&
    ) WHERE (region_id IS NOT NULL)
);

COMMENT ON TABLE public.supply_route_assignments IS
    'Asigna una supply_route a una región o tienda con prioridad y vigencia temporal. '
    'Una tienda puede tener override (store_id) que tiene precedencia sobre la asignación regional. '
    'NUNCA se actualiza supply_route_id en una fila existente — se cierra con valid_until y se inserta nueva.';

COMMENT ON COLUMN public.supply_route_assignments.priority IS
    '1 = ruta primaria (default). 2 = alternativa (entra si primaria falla). '
    'Solo una ruta por prioridad puede estar vigente en el mismo scope al mismo tiempo '
    '(garantizado por EXCLUDE constraint).';

COMMENT ON COLUMN public.supply_route_assignments.valid_until IS
    'NULL significa vigente actualmente. Para cambiar la ruta: '
    'UPDATE SET valid_until = CURRENT_DATE WHERE valid_until IS NULL, '
    'luego INSERT nueva asignación con valid_from = CURRENT_DATE.';

-- Índice principal: rutas primarias vigentes por región
CREATE INDEX idx_sra_region_active_primary
    ON public.supply_route_assignments(region_id, priority)
    WHERE valid_until IS NULL
      AND region_id IS NOT NULL;

-- Índice para overrides de tienda vigentes
CREATE INDEX idx_sra_store_active
    ON public.supply_route_assignments(store_id, priority)
    WHERE valid_until IS NULL
      AND store_id IS NOT NULL;

-- Índice para consultas históricas por fecha
CREATE INDEX idx_sra_valid_from
    ON public.supply_route_assignments(valid_from, valid_until);
```

### Procedimiento para cambiar una ruta (nunca UPDATE directo)

```sql
-- ════════════════════════════════════════════════════════════════
-- PROCEDIMIENTO: cambiar la ruta primaria de una región
-- SIEMPRE usar este patrón. Nunca hacer UPDATE de supply_route_id.
-- ════════════════════════════════════════════════════════════════

BEGIN;

-- Paso 1: cerrar la asignación vigente
UPDATE public.supply_route_assignments
SET    valid_until   = CURRENT_DATE,
       change_reason = :motivo        -- 'precio', 'desabastecimiento', etc.
WHERE  region_id     = :region_id
  AND  priority      = 1
  AND  valid_until   IS NULL;

-- Verificar que se cerró exactamente una fila
-- (si son 0 filas, no había asignación vigente — revisar datos)

-- Paso 2: insertar la nueva asignación
INSERT INTO public.supply_route_assignments
    (supply_route_id, region_id, priority, valid_from, assigned_by, change_reason)
VALUES
    (:nueva_ruta_id, :region_id, 1, CURRENT_DATE, :usuario, :motivo);

COMMIT;
```

### Verificación de Fase 2

```sql
-- Verificar tablas
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('supply_routes', 'supply_route_assignments');

-- Verificar que btree_gist está habilitado
SELECT extname FROM pg_extension WHERE extname = 'btree_gist';

-- Verificar constraints EXCLUDE
SELECT conname, contype, conrelid::regclass
FROM pg_constraint
WHERE contype = 'x'   -- 'x' = exclusion constraint
  AND conrelid::regclass::text LIKE '%supply_route%';
-- Resultado esperado: 2 filas (una por scope: store, region)

-- Test: insertar dos asignaciones con misma prioridad en mismo scope
-- Esto DEBE fallar con error de constraint
INSERT INTO public.supply_route_assignments
    (supply_route_id, region_id, priority, valid_from, assigned_by)
VALUES (1, 1, 1, '2024-01-01', 'test');

INSERT INTO public.supply_route_assignments
    (supply_route_id, region_id, priority, valid_from, assigned_by)
VALUES (2, 1, 1, '2024-06-01', 'test');
-- DEBE lanzar: ERROR: conflicting key value violates exclusion constraint
-- Si no falla, el EXCLUDE constraint no está funcionando — DETENER la implementación
```

---

## 7. Fase 3 — Referencias externas, unidades y precios

### Propósito

Resolver tres problemas simultáneos:
1. El mismo ingrediente tiene nombres y códigos distintos por proveedor (`ingredient_supplier_refs`).
2. La unidad de compra del proveedor no es la misma que la unidad de receta de Qargo (`supplier_unit_conversions`).
3. Los precios tienen precio de lista público y precio negociado por Qargo, en moneda explícita, con vigencia temporal (`supply_route_prices`).

### Migración

```sql
-- ─────────────────────────────────────────────────────────────────
-- TABLA: ingredient_supplier_refs
-- Cómo se llama y qué código tiene un ingrediente canónico
-- en el catálogo de un proveedor específico.
-- Un ingrediente canónico puede tener N referencias externas
-- (una por cada ruta de suministro que lo provee).
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE public.ingredient_supplier_refs (
    id               SERIAL       PRIMARY KEY,
    ingredient_id    INTEGER      NOT NULL
                                  REFERENCES public.ingredients(id),
    supply_route_id  INTEGER      NOT NULL
                                  REFERENCES public.supply_routes(id),
    external_name    VARCHAR(300) NOT NULL,
    -- Nombre exacto como aparece en el catálogo/factura del proveedor
    external_code    VARCHAR(100),
    -- SKU, EAN, referencia del catálogo del proveedor
    -- Puede ser NULL si el proveedor no maneja códigos
    purchase_unit    VARCHAR(100) NOT NULL,
    -- Unidad en que el proveedor vende: 'bolsa 5kg', 'caja 12 un', 'litro', etc.
    units_per_pack   NUMERIC,
    -- Cuántas unidades base contiene el empaque de compra
    -- Ej: si purchase_unit = 'caja 12 un', units_per_pack = 12
    is_active        BOOLEAN      NOT NULL DEFAULT true,
    notes            TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),

    UNIQUE (supply_route_id, external_code)
    -- El mismo proveedor no puede tener el mismo código para dos ingredientes distintos
    -- (en caso de que external_code sea NULL, se permite duplicado — no es PK)
);

COMMENT ON TABLE public.ingredient_supplier_refs IS
    'Nombre externo, código y unidad de compra de un ingrediente según un proveedor específico. '
    'Resuelve que el mismo ingrediente se llame diferente y tenga IDs distintos por proveedor. '
    'Un ingrediente canónico puede tener una referencia por cada supply_route que lo provea.';

CREATE TRIGGER trg_ingredient_supplier_refs_updated_at
    BEFORE UPDATE ON public.ingredient_supplier_refs
    FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();

CREATE INDEX idx_isr_ingredient
    ON public.ingredient_supplier_refs(ingredient_id)
    WHERE is_active = true;

CREATE INDEX idx_isr_route
    ON public.ingredient_supplier_refs(supply_route_id)
    WHERE is_active = true;


-- ─────────────────────────────────────────────────────────────────
-- TABLA: supplier_unit_conversions
-- Convierte la unidad de compra del proveedor a la unidad
-- canónica de receta de Qargo.
-- Reemplaza y extiende ingredient_recipe_unit_conversions,
-- que era unidireccional y sin contexto de proveedor.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE public.supplier_unit_conversions (
    id                 SERIAL      PRIMARY KEY,
    ingredient_ref_id  INTEGER     NOT NULL
                                   REFERENCES public.ingredient_supplier_refs(id),
    recipe_unit_id     INTEGER     NOT NULL
                                   REFERENCES public.recipe_units(id),
    purchase_qty       NUMERIC     NOT NULL,
    -- Cuántas unidades de compra del proveedor...
    recipe_qty         NUMERIC     NOT NULL,
    -- ...equivalen a cuántas unidades de receta canónica de Qargo
    -- Ejemplo: 1 bolsa 5kg del proveedor = 5000 gramos en receta
    notes              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT suc_quantities_positive CHECK (
        purchase_qty > 0 AND recipe_qty > 0
    ),

    UNIQUE (ingredient_ref_id, recipe_unit_id)
    -- Una referencia de proveedor tiene una sola conversión por unidad de receta
);

COMMENT ON TABLE public.supplier_unit_conversions IS
    'Conversión entre la unidad de compra del proveedor y la unidad canónica de receta de Qargo. '
    'Columnas: purchase_qty unidades del proveedor = recipe_qty unidades de receta. '
    'Ejemplo: purchase_qty=1, purchase_unit="bolsa 5kg", recipe_qty=5000, recipe_unit="gramo".';


-- ─────────────────────────────────────────────────────────────────
-- TABLA: supply_route_prices
-- Precio de un ingrediente en una ruta específica.
-- Incluye precio de lista (público) y precio Qargo (negociado),
-- moneda explícita y vigencia temporal sin solapamiento.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE public.supply_route_prices (
    id                SERIAL      PRIMARY KEY,
    supply_route_id   INTEGER     NOT NULL
                                  REFERENCES public.supply_routes(id),
    list_price        NUMERIC     NOT NULL,
    -- Precio de catálogo público del proveedor
    qargo_price       NUMERIC     NOT NULL,
    -- Precio negociado por Qargo (por volumen, contrato, etc.)
    -- Si no hay negociación, qargo_price = list_price
    currency_code     CHAR(3)     NOT NULL,
    -- ISO 4217: 'COP', 'USD', 'EUR'
    price_per_unit    VARCHAR(100) NOT NULL,
    -- Unidad a la que aplica el precio: 'por kg', 'por caja', 'por litro'
    -- Debe coincidir con purchase_unit del ingredient_supplier_ref asociado
    valid_from        DATE        NOT NULL DEFAULT CURRENT_DATE,
    valid_until       DATE,
    -- NULL = precio vigente actualmente
    source            VARCHAR(100),
    -- 'contrato_2024', 'factura_ref_123', 'cotizacion_email', etc.
    created_by        VARCHAR(100) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT srp_prices_positive CHECK (
        list_price > 0 AND qargo_price > 0
    ),

    CONSTRAINT srp_qargo_lte_list CHECK (
        qargo_price <= list_price
    ),
    -- El precio Qargo nunca puede ser mayor que el precio de lista.
    -- Si ocurre, es un error de entrada de datos.

    CONSTRAINT srp_currency_valid CHECK (
        currency_code ~ '^[A-Z]{3}$'
    ),

    -- No puede haber dos precios vigentes simultáneamente para la misma ruta
    EXCLUDE USING gist (
        supply_route_id WITH =,
        daterange(valid_from, COALESCE(valid_until, '9999-12-31'::date), '[)') WITH &&
    )
);

COMMENT ON TABLE public.supply_route_prices IS
    'Precio de un ingrediente en una ruta de suministro. Incluye precio de lista y precio '
    'negociado por Qargo, con moneda ISO 4217 y vigencia sin solapamiento. '
    'NUNCA actualizar filas existentes — cerrar con valid_until e insertar nueva.';

COMMENT ON COLUMN public.supply_route_prices.qargo_price IS
    'Precio negociado por volumen o contrato. Si no hay negociación especial, '
    'debe ser igual a list_price. No puede ser mayor que list_price (CHECK constraint).';

CREATE INDEX idx_srp_route_active
    ON public.supply_route_prices(supply_route_id)
    WHERE valid_until IS NULL;
```

### Procedimiento para actualizar un precio

```sql
-- ════════════════════════════════════════════════════════════════
-- PROCEDIMIENTO: actualizar precio de una ruta
-- NUNCA hacer UPDATE del precio. Siempre cerrar + insertar.
-- ════════════════════════════════════════════════════════════════

BEGIN;

UPDATE public.supply_route_prices
SET    valid_until = CURRENT_DATE
WHERE  supply_route_id = :route_id
  AND  valid_until IS NULL;

INSERT INTO public.supply_route_prices
    (supply_route_id, list_price, qargo_price, currency_code,
     price_per_unit, valid_from, source, created_by)
VALUES
    (:route_id, :list_price, :qargo_price, :currency,
     :per_unit, CURRENT_DATE, :fuente, :usuario);

COMMIT;
```

### Verificación de Fase 3

```sql
-- Verificar tablas
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
      'ingredient_supplier_refs',
      'supplier_unit_conversions',
      'supply_route_prices'
  );

-- Verificar constraint qargo_price <= list_price
-- Debe fallar:
INSERT INTO public.supply_route_prices
    (supply_route_id, list_price, qargo_price, currency_code,
     price_per_unit, valid_from, created_by)
VALUES (1, 100, 150, 'COP', 'por kg', CURRENT_DATE, 'test');
-- DEBE fallar con: ERROR: new row violates check constraint "srp_qargo_lte_list"
```

---

## 8. Fase 4 — Disponibilidad regional y sustitutos

### Propósito

Registrar el estado de disponibilidad de ingredientes por ruta o región (`ingredient_availability`) y definir los sustitutos aprobados por corporativo cuando un ingrediente no está disponible (`ingredient_substitutes`).

**Sobre `ingredient_availability`:** Hoy no se toman decisiones modelables con este dato, pero se registra para habilitar modelos futuros de predicción y planificación. No tiene lógica de decisión — es solo observación y registro.

**Sobre `ingredient_substitutes`:** Solo corporativo puede definir sustitutos. La tabla lo refleja con `approved_by`. El campo `cost_impact_pct` permite calcular el impacto en el costo de la receta cuando el sustituto está activo.

### Migración

```sql
-- ─────────────────────────────────────────────────────────────────
-- TABLA: ingredient_availability
-- Estado de disponibilidad de un ingrediente en una ruta o región.
-- No contiene lógica de decisión — es registro de realidad operativa.
-- Diseñada para habilitar modelos predictivos futuros.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE public.ingredient_availability (
    id               SERIAL       PRIMARY KEY,
    ingredient_id    INTEGER      NOT NULL
                                  REFERENCES public.ingredients(id),
    supply_route_id  INTEGER
                                  REFERENCES public.supply_routes(id),
    -- NULL = aplica a toda la región (no a una ruta específica)
    region_id        INTEGER
                                  REFERENCES public.regions(id),
    -- NULL = aplica a nivel de ruta específica (supply_route_id NOT NULL)
    status           VARCHAR(50)  NOT NULL,
    -- Valores permitidos: 'available', 'shortage', 'discontinued', 'seasonal'
    -- 'shortage'      = desabastecimiento temporal
    -- 'discontinued'  = el proveedor dejó de fabricar/distribuir
    -- 'seasonal'      = no disponible fuera de temporada
    expected_resume  DATE,
    -- Fecha estimada de reabastecimiento (solo aplica para 'shortage')
    valid_from       DATE         NOT NULL DEFAULT CURRENT_DATE,
    valid_until      DATE,
    -- NULL = estado vigente actualmente
    reported_by      VARCHAR(100),
    notes            TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT ia_scope_required CHECK (
        supply_route_id IS NOT NULL OR region_id IS NOT NULL
    ),

    CONSTRAINT ia_status_valid CHECK (
        status IN ('available', 'shortage', 'discontinued', 'seasonal')
    ),

    CONSTRAINT ia_resume_only_for_shortage CHECK (
        expected_resume IS NULL OR status = 'shortage'
    )
);

COMMENT ON TABLE public.ingredient_availability IS
    'Registro de disponibilidad de ingredientes por ruta o región. '
    'No contiene lógica de decisión — es observación operativa para habilitar '
    'modelos predictivos futuros. Estado actual = filas donde valid_until IS NULL.';

CREATE INDEX idx_ia_ingredient_active
    ON public.ingredient_availability(ingredient_id, status)
    WHERE valid_until IS NULL;

CREATE INDEX idx_ia_route_active
    ON public.ingredient_availability(supply_route_id)
    WHERE valid_until IS NULL AND supply_route_id IS NOT NULL;


-- ─────────────────────────────────────────────────────────────────
-- TABLA: ingredient_substitutes
-- Sustitutos aprobados por corporativo.
-- Define qué ingrediente reemplaza a cuál, bajo qué condición,
-- con qué ratio de cantidad y qué impacto tiene en el costo.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE public.ingredient_substitutes (
    id                       SERIAL       PRIMARY KEY,
    original_ingredient_id   INTEGER      NOT NULL
                                          REFERENCES public.ingredients(id),
    substitute_ingredient_id INTEGER      NOT NULL
                                          REFERENCES public.ingredients(id),
    approved_by              VARCHAR(100) NOT NULL,
    -- Nombre o cargo de quien en corporativo aprueba el sustituto
    approval_date            DATE         NOT NULL,
    activation_condition     VARCHAR(50)  NOT NULL DEFAULT 'shortage',
    -- 'shortage'     = activar solo cuando hay desabastecimiento
    -- 'unavailable'  = activar cuando el ingrediente no está disponible en la región
    -- 'always'       = puede usarse siempre como alternativa
    quantity_ratio           NUMERIC      NOT NULL DEFAULT 1.0,
    -- Cuánto sustituto se usa por cada unidad del ingrediente original
    -- Ej: 1.0 = misma cantidad, 0.9 = 10% menos, 1.1 = 10% más
    recipe_unit_id           INTEGER
                                          REFERENCES public.recipe_units(id),
    -- Unidad en que se expresa el quantity_ratio
    -- NULL = misma unidad que el ingrediente original
    cost_impact_pct          NUMERIC,
    -- Impacto porcentual en el costo de la receta al usar el sustituto
    -- Positivo = más caro, Negativo = más barato
    -- NULL = impacto no calculado aún
    affects_regions          INTEGER[],
    -- IDs de regions donde aplica este sustituto
    -- NULL = aplica globalmente en todas las regiones
    valid_from               DATE         NOT NULL DEFAULT CURRENT_DATE,
    valid_until              DATE,
    notes                    TEXT,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),

    -- Un ingrediente no puede ser sustituto de sí mismo
    CONSTRAINT is_no_self_substitute CHECK (
        original_ingredient_id <> substitute_ingredient_id
    ),

    CONSTRAINT is_activation_valid CHECK (
        activation_condition IN ('shortage', 'unavailable', 'always')
    ),

    CONSTRAINT is_ratio_positive CHECK (
        quantity_ratio > 0
    ),

    UNIQUE (original_ingredient_id, substitute_ingredient_id, valid_from)
    -- No puede haber dos aprobaciones del mismo par en la misma fecha
);

COMMENT ON TABLE public.ingredient_substitutes IS
    'Sustitutos de ingredientes aprobados exclusivamente por corporativo. '
    'Cuando activation_condition = shortage, se activa cuando ingredient_availability '
    'registra status = shortage para el ingrediente original. '
    'cost_impact_pct permite recalcular el costo de la receta al aplicar el sustituto.';

COMMENT ON COLUMN public.ingredient_substitutes.quantity_ratio IS
    'Cantidad de sustituto a usar por cada unidad del ingrediente original. '
    '1.0 = misma cantidad. 0.9 = usar 10% menos. 1.2 = usar 20% más.';

CREATE INDEX idx_is_original_active
    ON public.ingredient_substitutes(original_ingredient_id)
    WHERE valid_until IS NULL;
```

### Verificación de Fase 4

```sql
-- Verificar tablas
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('ingredient_availability', 'ingredient_substitutes');

-- Test: auto-sustituto debe fallar
INSERT INTO public.ingredient_substitutes
    (original_ingredient_id, substitute_ingredient_id,
     approved_by, approval_date, quantity_ratio)
VALUES (1, 1, 'test', CURRENT_DATE, 1.0);
-- DEBE fallar con: ERROR: new row violates check constraint "is_no_self_substitute"

-- Test: activation_condition inválida debe fallar
INSERT INTO public.ingredient_substitutes
    (original_ingredient_id, substitute_ingredient_id,
     approved_by, approval_date, activation_condition, quantity_ratio)
VALUES (1, 2, 'test', CURRENT_DATE, 'cuando_quiera', 1.0);
-- DEBE fallar con: ERROR: new row violates check constraint "is_activation_valid"
```

---

## 9. Fase 5 — Historial de relaciones y snapshots de costo

### Propósito

`store_supplier_history` registra qué ruta usó cada tienda para cada ingrediente, cuándo cambió y por qué. Es el log de auditoría de relaciones comerciales.

`recipe_cost_snapshots` registra cada cálculo de costo de una receta por tienda, incluyendo qué ruta se usó y si había sustitutos activos. Es inmutable — no se actualiza, solo se inserta.

### Migración

```sql
-- ─────────────────────────────────────────────────────────────────
-- TABLA: store_supplier_history
-- Historial completo de qué ruta usó cada tienda para cada
-- ingrediente. Permite auditar cuándo y por qué se cambió
-- de distribuidor o fabricante.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE public.store_supplier_history (
    id               SERIAL       PRIMARY KEY,
    store_id         INTEGER      NOT NULL
                                  REFERENCES public.stores(id),
    ingredient_id    INTEGER      NOT NULL
                                  REFERENCES public.ingredients(id),
    supply_route_id  INTEGER      NOT NULL
                                  REFERENCES public.supply_routes(id),
    valid_from       DATE         NOT NULL,
    valid_until      DATE,
    -- NULL = relación vigente actualmente
    change_reason    VARCHAR(200),
    changed_by       VARCHAR(100),
    notes            TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),

    -- Una tienda solo puede tener una ruta activa por ingrediente en un período
    EXCLUDE USING gist (
        store_id      WITH =,
        ingredient_id WITH =,
        daterange(valid_from, COALESCE(valid_until, '9999-12-31'::date), '[)') WITH &&
    )
);

COMMENT ON TABLE public.store_supplier_history IS
    'Historial de qué ruta de suministro usó cada tienda para cada ingrediente. '
    'El EXCLUDE constraint garantiza que una tienda no puede tener dos rutas activas '
    'simultáneamente para el mismo ingrediente — bug que destruye reportes de costos.';

CREATE INDEX idx_ssh_store_ingredient_active
    ON public.store_supplier_history(store_id, ingredient_id)
    WHERE valid_until IS NULL;


-- ─────────────────────────────────────────────────────────────────
-- TABLA: recipe_cost_snapshots
-- Registro inmutable de cada cálculo de costo de una receta
-- por tienda. Incluye desglose completo en JSONB.
-- Alimenta product_price_history del schema existente.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE public.recipe_cost_snapshots (
    id                SERIAL       PRIMARY KEY,
    product_id        INTEGER      NOT NULL
                                   REFERENCES public.products(id),
    store_id          INTEGER      NOT NULL
                                   REFERENCES public.stores(id),
    calculated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    base_cost         NUMERIC      NOT NULL,
    -- Costo sin sustitutos activos, usando rutas primarias
    effective_cost    NUMERIC      NOT NULL,
    -- Costo real incluyendo sustitutos y ruta efectivamente usada
    currency_code     CHAR(3)      NOT NULL,
    has_substitutes   BOOLEAN      NOT NULL DEFAULT false,
    -- true = al menos un ingrediente fue reemplazado por un sustituto
    snapshot_detail   JSONB        NOT NULL,
    -- Desglose completo por ingrediente:
    -- [{
    --   "ingredient_id": 1,
    --   "ingredient_name": "leche entera",
    --   "supply_route_id": 5,
    --   "is_substitute": false,
    --   "original_ingredient_id": null,
    --   "quantity": 200,
    --   "unit": "ml",
    --   "unit_cost": 0.0025,
    --   "subtotal": 0.50
    -- }]
    triggered_by      VARCHAR(100),
    -- 'price_change', 'route_change', 'substitute_activated', 'manual', 'scheduled'

    CONSTRAINT rcs_costs_positive CHECK (
        base_cost > 0 AND effective_cost > 0
    )
);

COMMENT ON TABLE public.recipe_cost_snapshots IS
    'Registro INMUTABLE de cálculos de costo de recetas por tienda. '
    'Nunca se actualiza — solo se inserta. snapshot_detail en JSONB '
    'permite auditar el desglose exacto del cálculo sin reconstruirlo. '
    'Alimenta product_price_history al disparar cambios de precio.';

-- Sin UPDATE trigger — esta tabla es append-only por diseño
CREATE INDEX idx_rcs_product_store
    ON public.recipe_cost_snapshots(product_id, store_id, calculated_at DESC);

CREATE INDEX idx_rcs_store_date
    ON public.recipe_cost_snapshots(store_id, calculated_at DESC);
```

---

## 10. Fase 6 — Modificaciones al schema existente

### Propósito

Corregir los problemas identificados en el schema original y conectarlo con las nuevas tablas. **Ejecutar cada bloque en una transacción separada.**

### 6.1 — Tabla `categories` y normalización de categorías

```sql
-- ─────────────────────────────────────────────────────────────────
-- PROBLEMA: category_margins y products usan category VARCHAR
-- sin FK entre ellas. Un typo hace que un margen nunca aplique.
-- SOLUCIÓN: tabla categories como entidad normalizada.
-- ─────────────────────────────────────────────────────────────────

BEGIN;

CREATE TABLE public.categories (
    id          SERIAL       PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    is_active   BOOLEAN      NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Poblar desde los valores existentes en products
INSERT INTO public.categories (name)
SELECT DISTINCT category
FROM public.products
WHERE category IS NOT NULL
ON CONFLICT (name) DO NOTHING;

-- También desde category_margins
INSERT INTO public.categories (name)
SELECT DISTINCT category
FROM public.category_margins
WHERE category IS NOT NULL
ON CONFLICT (name) DO NOTHING;

-- Agregar FK en products
ALTER TABLE public.products
    ADD COLUMN category_id INTEGER REFERENCES public.categories(id);

UPDATE public.products p
SET    category_id = c.id
FROM   public.categories c
WHERE  c.name = p.category;

-- Agregar FK en category_margins
ALTER TABLE public.category_margins
    ADD COLUMN category_id INTEGER REFERENCES public.categories(id);

UPDATE public.category_margins cm
SET    category_id = c.id
FROM   public.categories c
WHERE  c.name = cm.category;

-- NOTA: No eliminar la columna category VARCHAR todavía.
-- Marcarla como deprecated y migrar el código de aplicación primero.
-- Eliminar en una migración posterior cuando el código no la use.
COMMENT ON COLUMN public.products.category IS
    'DEPRECATED: usar category_id (FK a categories). '
    'Esta columna se eliminará en la próxima migración mayor.';

COMMENT ON COLUMN public.category_margins.category IS
    'DEPRECATED: usar category_id (FK a categories). '
    'Esta columna se eliminará en la próxima migración mayor.';

COMMIT;
```

### 6.2 — Agregar `region_id` y `default_currency_code` a `stores`

```sql
BEGIN;

ALTER TABLE public.stores
    ADD COLUMN region_id             INTEGER REFERENCES public.regions(id),
    ADD COLUMN default_currency_code CHAR(3) NOT NULL DEFAULT 'COP';

COMMENT ON COLUMN public.stores.region_id IS
    'Región geográfica a la que pertenece la tienda. '
    'Usada por fn_resolve_supply_route para encontrar la asignación regional '
    'cuando no hay override de tienda.';

COMMENT ON COLUMN public.stores.default_currency_code IS
    'Moneda por defecto para transacciones de esta tienda. ISO 4217.';

-- Poblar region_id con datos reales una vez que la tabla regions esté poblada
-- UPDATE public.stores SET region_id = :region_id WHERE city = :ciudad;

COMMIT;
```

### 6.3 — Agregar `updated_at` a tablas de receta

```sql
BEGIN;

-- recipe_ingredients
ALTER TABLE public.recipe_ingredients
    ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE TRIGGER trg_recipe_ingredients_updated_at
    BEFORE UPDATE ON public.recipe_ingredients
    FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();

-- product_sizes
ALTER TABLE public.product_sizes
    ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE TRIGGER trg_product_sizes_updated_at
    BEFORE UPDATE ON public.product_sizes
    FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();

-- recipe_sub_recipes
ALTER TABLE public.recipe_sub_recipes
    ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE TRIGGER trg_recipe_sub_recipes_updated_at
    BEFORE UPDATE ON public.recipe_sub_recipes
    FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();

-- ingredients
ALTER TABLE public.ingredients
    ADD COLUMN canonical_unit VARCHAR(100),
    ADD COLUMN updated_at     TIMESTAMPTZ NOT NULL DEFAULT now();

COMMENT ON COLUMN public.ingredients.canonical_unit IS
    'Unidad interna de Qargo para este ingrediente, independiente de '
    'cómo lo compre cada proveedor. Ej: gramo, mililitro, unidad.';

CREATE TRIGGER trg_ingredients_updated_at
    BEFORE UPDATE ON public.ingredients
    FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();

COMMIT;
```

### 6.4 — Corregir `product_pricing`

```sql
BEGIN;

-- Agregar moneda
ALTER TABLE public.product_pricing
    ADD COLUMN currency_code CHAR(3) NOT NULL DEFAULT 'COP';

-- Agregar UNIQUE constraint
-- COALESCE(-1) para manejar store_id nullable en el unique
CREATE UNIQUE INDEX uix_product_pricing_current
    ON public.product_pricing (product_id, size_id, COALESCE(store_id, -1), effective_date);

COMMENT ON INDEX uix_product_pricing_current IS
    'Garantiza que no puede haber dos precios para el mismo producto/talla/tienda/fecha. '
    'store_id NULL (precio global) se trata como -1 en el índice.';

COMMIT;
```

### 6.5 — Agregar `currency_code` a `product_price_history`

```sql
BEGIN;

ALTER TABLE public.product_price_history
    ADD COLUMN currency_code CHAR(3) NOT NULL DEFAULT 'COP';

COMMIT;
```

---

## 11. Función de resolución de ruta

Esta es la implementación del **Principio 6** — una sola fuente de verdad para la lógica de resolución. Todo el sistema (pricing engine, reportes, ETL) llama esta función. Si la lógica cambia, se cambia aquí.

```sql
-- ─────────────────────────────────────────────────────────────────
-- FUNCIÓN: fn_resolve_supply_route
-- Determina qué ruta de suministro debe usar una tienda específica
-- para un ingrediente en una fecha dada.
--
-- LÓGICA DE RESOLUCIÓN:
--   1. Busca override explícito de tienda (store_id = p_store_id)
--   2. Si no existe, usa la asignación de la región de la tienda
--   3. Dentro del mismo scope, menor priority gana (1 = primaria)
--   4. Solo retorna rutas vigentes en p_date
--
-- RETORNA:
--   - La ruta ganadora (1 fila máximo)
--   - scope: 'store_override' | 'region_default'
--   - priority: 1 (primaria), 2 (alternativa), etc.
-- ─────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.fn_resolve_supply_route(
    p_ingredient_id  INTEGER,
    p_store_id       INTEGER,
    p_date           DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    assignment_id    INTEGER,
    supply_route_id  INTEGER,
    scope            VARCHAR,
    priority         INTEGER,
    manufacturer_id  INTEGER,
    distributor_id   INTEGER,
    is_direct        BOOLEAN
)
LANGUAGE sql STABLE AS $$

    -- ── Candidato 1: override explícito de tienda ─────────────────
    SELECT
        sra.id                  AS assignment_id,
        sra.supply_route_id,
        'store_override'::VARCHAR AS scope,
        sra.priority,
        sr.manufacturer_id,
        sr.distributor_id,
        sr.is_direct
    FROM public.supply_route_assignments sra
    JOIN public.supply_routes            sr  ON sr.id = sra.supply_route_id
    WHERE sra.store_id     = p_store_id
      AND sr.ingredient_id = p_ingredient_id
      AND sr.is_active     = true
      AND sra.valid_from  <= p_date
      AND (sra.valid_until IS NULL OR sra.valid_until > p_date)

    UNION ALL

    -- ── Candidato 2: asignación regional de la tienda ─────────────
    SELECT
        sra.id                   AS assignment_id,
        sra.supply_route_id,
        'region_default'::VARCHAR AS scope,
        sra.priority,
        sr.manufacturer_id,
        sr.distributor_id,
        sr.is_direct
    FROM public.supply_route_assignments sra
    JOIN public.supply_routes            sr  ON sr.id = sra.supply_route_id
    JOIN public.stores                   s   ON s.region_id = sra.region_id
    WHERE s.id             = p_store_id
      AND sra.store_id     IS NULL
      AND sr.ingredient_id = p_ingredient_id
      AND sr.is_active     = true
      AND sra.valid_from  <= p_date
      AND (sra.valid_until IS NULL OR sra.valid_until > p_date)

    ORDER BY
        -- store_override siempre tiene precedencia sobre region_default
        CASE scope WHEN 'store_override' THEN 0 ELSE 1 END,
        priority ASC

    LIMIT 1;
$$;

COMMENT ON FUNCTION public.fn_resolve_supply_route IS
    'Fuente única de verdad para determinar qué ruta de suministro usa una tienda. '
    'Prioridad: override de tienda > asignación regional. '
    'Dentro del mismo scope: priority 1 (primaria) > priority 2 (alternativa). '
    'Todos los sistemas que necesiten esta respuesta deben llamar esta función.';
```

### Uso de la función

```sql
-- ¿Qué ruta usa la tienda 3 para el ingrediente 7 hoy?
SELECT * FROM public.fn_resolve_supply_route(7, 3);

-- ¿Qué ruta usaba la tienda 3 para el ingrediente 7 el 1 de enero de 2024?
SELECT * FROM public.fn_resolve_supply_route(7, 3, '2024-01-01');

-- Obtener la ruta y su precio vigente para calcular costo
SELECT
    r.supply_route_id,
    r.scope,
    r.priority,
    p.qargo_price,
    p.currency_code,
    p.price_per_unit
FROM public.fn_resolve_supply_route(7, 3) r
JOIN public.supply_route_prices p
  ON p.supply_route_id = r.supply_route_id
 AND p.valid_until IS NULL;
```

---

## 12. Índices y performance

Ejecutar después de todas las migraciones. Estos índices cubren los patrones de query más frecuentes del negocio.

```sql
-- ─── supply_routes ────────────────────────────────────────────────
-- Ya creado en Fase 2, verificar que exista:
-- idx_supply_routes_ingredient_active

-- ─── supply_route_assignments ─────────────────────────────────────
-- Ya creados en Fase 2:
-- idx_sra_region_active_primary
-- idx_sra_store_active
-- idx_sra_valid_from

-- ─── supply_route_prices ──────────────────────────────────────────
-- Ya creado en Fase 3:
-- idx_srp_route_active

-- ─── ingredient_availability ──────────────────────────────────────
-- Ya creados en Fase 4:
-- idx_ia_ingredient_active
-- idx_ia_route_active

-- ─── ingredient_substitutes ───────────────────────────────────────
-- Ya creado en Fase 4:
-- idx_is_original_active

-- ─── ÍNDICES ADICIONALES DE CONSULTA ─────────────────────────────

-- competitor_products: scraping frecuente acumula filas rápido
CREATE INDEX IF NOT EXISTS idx_competitor_products_scraped
    ON public.competitor_products(competitor_id, scraped_at DESC);

-- product_pricing: query de precio vigente por tienda
CREATE INDEX IF NOT EXISTS idx_product_pricing_current
    ON public.product_pricing(product_id, store_id, effective_date DESC)
    WHERE store_id IS NOT NULL;

-- store_supplier_history: auditoría por tienda
CREATE INDEX IF NOT EXISTS idx_ssh_store_active
    ON public.store_supplier_history(store_id)
    WHERE valid_until IS NULL;

-- recipe_cost_snapshots: costo más reciente por producto/tienda
-- Ya creado en Fase 5: idx_rcs_product_store
```

---

## 13. Verificación post-implementación

Ejecutar este bloque completo después de terminar todas las fases. Debe retornar únicamente resultados positivos.

```sql
-- ═══════════════════════════════════════════════════════════════
-- VERIFICACIÓN COMPLETA POST-IMPLEMENTACIÓN
-- Todos los resultados deben ser como se indica en los comentarios
-- ═══════════════════════════════════════════════════════════════

-- 1. Todas las tablas nuevas existen
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
      'regions', 'manufacturers', 'distributors',
      'supply_routes', 'supply_route_assignments',
      'ingredient_supplier_refs', 'supplier_unit_conversions',
      'supply_route_prices', 'ingredient_availability',
      'ingredient_substitutes', 'store_supplier_history',
      'recipe_cost_snapshots', 'categories'
  )
ORDER BY table_name;
-- Resultado esperado: 13 filas

-- 2. Extensión btree_gist habilitada
SELECT extname FROM pg_extension WHERE extname = 'btree_gist';
-- Resultado esperado: 1 fila

-- 3. Todos los EXCLUDE constraints existen
SELECT conname, conrelid::regclass::text AS tabla
FROM pg_constraint
WHERE contype = 'x'
ORDER BY tabla, conname;
-- Resultado esperado: mínimo 5 constraints EXCLUDE:
--   supply_route_assignments (2: por store_id, por region_id)
--   supply_route_prices (1)
--   store_supplier_history (1)
-- Cualquier número menor indica una fase fallida

-- 4. Función de resolución existe
SELECT routine_name, routine_type
FROM information_schema.routines
WHERE routine_schema = 'public'
  AND routine_name = 'fn_resolve_supply_route';
-- Resultado esperado: 1 fila, routine_type = FUNCTION

-- 5. Triggers updated_at existen en tablas nuevas y modificadas
SELECT trigger_name, event_object_table
FROM information_schema.triggers
WHERE trigger_schema = 'public'
  AND trigger_name LIKE 'trg_%_updated_at'
ORDER BY event_object_table;
-- Resultado esperado: mínimo 8 triggers

-- 6. stores tiene region_id y default_currency_code
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'stores'
  AND column_name IN ('region_id', 'default_currency_code')
ORDER BY column_name;
-- Resultado esperado: 2 filas

-- 7. product_pricing tiene currency_code y unique index
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'product_pricing'
  AND column_name = 'currency_code';
-- Resultado esperado: 1 fila

SELECT indexname
FROM pg_indexes
WHERE tablename = 'product_pricing'
  AND indexname = 'uix_product_pricing_current';
-- Resultado esperado: 1 fila

-- 8. Verificar integridad: no hay supply_routes con source incoherente
SELECT id
FROM public.supply_routes
WHERE is_direct = false
  AND manufacturer_id IS NULL
  AND distributor_id IS NULL;
-- Resultado esperado: 0 filas (si hay datos de prueba)

SELECT id
FROM public.supply_routes
WHERE is_direct = true
  AND distributor_id IS NOT NULL;
-- Resultado esperado: 0 filas
```

---

## 14. Bugs que este modelo previene explícitamente

| Bug | Mecanismo de prevención |
|---|---|
| Dos rutas primarias vigentes para el mismo scope | `EXCLUDE USING gist` en `supply_route_assignments` |
| Dos precios vigentes para la misma ruta | `EXCLUDE USING gist` en `supply_route_prices` |
| Ruta directa con distribuidor asignado | `CHECK supply_routes_direct_no_distributor` |
| Precio Qargo mayor que precio de lista | `CHECK srp_qargo_lte_list` |
| Sustituto de un ingrediente consigo mismo | `CHECK is_no_self_substitute` |
| Margen de categoría que nunca aplica por typo | FK `category_id` en `products` y `category_margins` |
| Dos precios para el mismo producto/talla/tienda/fecha | `UNIQUE INDEX uix_product_pricing_current` |
| Dos rutas activas simultáneas para misma tienda/ingrediente | `EXCLUDE USING gist` en `store_supplier_history` |
| Precio sin moneda en cualquier tabla | `currency_code CHAR(3) NOT NULL` en todas las tablas de precio |
| Lógica de resolución divergente entre sistemas | `fn_resolve_supply_route` como única fuente de verdad |
| Actualización silenciosa de historial | Patrón `valid_until + INSERT` obligatorio; nunca UPDATE de datos de negocio |

---

## 15. Lo que queda intencionalmente sin modelar

Estos conceptos tienen el andamiaje preparado pero **no se modelan hoy** porque el negocio aún no tiene procesos formales para ellos. Cuando maduren, se agregan sin romper lo existente.

| Concepto | Andamiaje actual | Cómo expandir cuando sea necesario |
|---|---|---|
| Criterio de selección de proveedor preferido | `priority INTEGER` en `supply_route_assignments` + `metadata JSONB` en `distributors` | Agregar tabla `supplier_scoring` con criterios ponderados; `priority` se calcula desde ahí |
| Desabastecimiento como trigger de sustituto | `ingredient_availability.status = 'shortage'` existe | Agregar función `fn_activate_substitutes(store_id, date)` que lee availability y retorna sustitutos activos |
| Compra consolidada por volumen entre tiendas | `qargo_price` en `supply_route_prices` ya captura el precio negociado | Agregar tabla `volume_tiers` con umbrales y precios escalonados; `supply_route_prices` referencia el tier activo |
| Scoring de confiabilidad de proveedor | `metadata JSONB` en `manufacturers` y `distributors` | Agregar tabla `supplier_reliability_events` con eventos (entrega tarde, calidad baja) y tabla `supplier_scores` calculados |
| Lead time y predicción de desabastecimiento | `ingredient_availability` registra los datos base | Agregar `average_lead_days` en `supply_routes` + modelo predictivo externo que lee `ingredient_availability` |