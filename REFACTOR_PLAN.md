# Plan de Refactorización — Qargo Coffee

> **Documento de implementación para Claude Code.**
> Generado el 2026-06-04 a partir del análisis en `files/analisis_y_refactor_qargo.md`,
> el ERD en `files/qargo_erd.mermaid` y el DDL objetivo en `files/schema_refactorizado.sql`.
> Los datos de Supabase son de prueba → se puede hacer reset limpio (greenfield).

---

## Premisas del plan

1. **Greenfield**: borrar los datos existentes en Supabase permite implementar el esquema objetivo de una sola vez, sin backfill incremental. El historial de migraciones de Alembic se reemplaza por una única migración `0001_initial_schema`.
2. **El DDL objetivo está validado**: `files/schema_refactorizado.sql` fue ejecutado de principio a fin contra PostgreSQL 16 (37 tablas, 91 FKs, 65 UNIQUEs, 39 CHECKs, 1 EXCLUDE, 119 índices, 23 triggers, 4 vistas).
3. **El backend sigue siendo SQLAlchemy + FastAPI + Alembic + Jinja2/HTMX**. No cambia la arquitectura de la aplicación, solo la capa de datos.
4. **Orden de implementación**: BD → Modelos → Schemas → Routers → Servicios → Tests. Cada etapa debe pasar los tests antes de avanzar.

---

## Cambios estructurales respecto al esquema actual

| Cambio | Detalle |
|--------|---------|
| Nueva tabla `currencies` | FK target para todo lo monetario. Semilla: COP, USD, EUR |
| Nueva tabla `ingredient_substitute_regions` | Reemplaza `affects_regions ARRAY` de `ingredient_substitutes` (integridad referencial real) |
| `integer` → `bigint IDENTITY` | En todas las tablas de alto crecimiento (historiales, snapshots, competitor_products) |
| Dominios de tipos | `price_amount` = `numeric(14,4)≥0`, `quantity_amount` = `numeric(14,6)≥0`, `pct_amount` = `numeric(6,3)`, `iso_country` = `char(2)` |
| 4 tablas particionadas por fecha | `ingredient_price_history`, `product_price_history`, `recipe_cost_snapshots`, `competitor_products` (PK compuesta incluye columna de partición) |
| `product_competitor_matches` FK compuesta | Referencia a `competitor_products(id, scraped_at)` por ser tabla particionada |
| Índices en todas las FKs | PostgreSQL no crea índices automáticos en FKs. Se añade uno por cada FK (~50) |
| Índices únicos parciales | `uq_product_sizes_default (product_id) WHERE is_default`, `uq_product_pricing_current` con COALESCE |
| `ON DELETE`/`ON UPDATE` explícitos | CASCADE/RESTRICT/SET NULL según semántica de la relación |
| `NOT NULL DEFAULT` en booleanos | Elimina el estado `NULL` en flags: `is_active`, `is_default`, `scales_with_size`, etc. |
| `CHECK` de rangos | Cantidades >0, porcentajes entre 0-100, factores >0, prioridades ≥1 |
| `UNIQUE` en 10 tablas de cruce | `recipe_ingredients`, `store_products`, `product_sizes`, etc. (ver sección 2.2 del análisis) |
| 4 vistas nuevas | `v_current_ingredient_price`, `v_product_modifier_cost`, `v_current_ingredient_availability`, `v_product_effective_price` |
| Función + 23 triggers `set_updated_at` | Mantiene `updated_at` automáticamente en todas las tablas mutables |
| `category` como `varchar(slug)` PK en `categories` | FK con `ON UPDATE CASCADE` en `products` y `category_margins` |
| `stores.default_currency_code` → FK a `currencies` | Con `ON UPDATE CASCADE ON DELETE RESTRICT` |

---

## Etapa 0 — Preparación y reset (hacer primero)

### 0.1 Crear rama git
```bash
git checkout -b refactor/greenfield-schema
```

### 0.2 Resetear Supabase
Ejecutar en Supabase SQL editor (o via psql con la URI de producción):
```sql
-- Eliminar todas las tablas del schema público en orden inverso de dependencias
-- O usar la opción nuclear si es base de datos de dev exclusiva:
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO postgres;
GRANT ALL ON SCHEMA public TO public;
```

### 0.3 Archivar migraciones actuales
```bash
# Mover versiones viejas a un directorio de archivo
mkdir -p backend/migrations/versions/_archived
mv backend/migrations/versions/*.py backend/migrations/versions/_archived/
```

---

## Etapa 1 — Migración inicial (greenfield)

**Objetivo**: crear una única migración Alembic `0001_initial_schema.py` que implementa todo el DDL del archivo `files/schema_refactorizado.sql`.

### Archivos a crear/modificar

| Archivo | Acción |
|---------|--------|
| `backend/migrations/versions/0001_initial_schema.py` | Crear. Contiene el DDL completo en `op.execute()` |

### Notas de implementación

- La migración usa `op.execute()` para ejecutar el SQL directamente. No usar autogenerate de Alembic para este caso (el DDL manual tiene constraints y features que Alembic no autogenera correctamente).
- El `down` de la migración hace `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` para reset limpio.
- Incluir la creación de `alembic_version` en el propio script (Alembic la gestiona, no duplicar).
- Los dominios (`CREATE DOMAIN`) son objetos de PostgreSQL que Alembic no conoce nativamente; crearlos vía `op.execute()`.
- Las tablas particionadas (`PARTITION BY RANGE`) también vía `op.execute()`.

### Estructura de la migración

```python
# backend/migrations/versions/0001_initial_schema.py
revision = '0001_initial_schema'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # 1. Extensions
    # 2. Domains
    # 3. Trigger function set_updated_at
    # 4. Todas las tablas (orden correcto de FKs)
    # 5. Todos los índices
    # 6. Todos los triggers (DO $$ block)
    # 7. Todas las vistas

def downgrade():
    op.execute("DROP SCHEMA public CASCADE")
    op.execute("CREATE SCHEMA public")
    op.execute("GRANT ALL ON SCHEMA public TO postgres")
    op.execute("GRANT ALL ON SCHEMA public TO public")
```

---

## Etapa 2 — Modelos SQLAlchemy

**Objetivo**: actualizar todos los modelos para reflejar el schema objetivo.

### 2.1 Nuevo archivo: `backend/models/currency.py`
```python
class Currency(Base):
    __tablename__ = "currencies"
    code: str = Column(String(3), primary_key=True)
    name: str = Column(String(64), nullable=False)
    minor_unit: int = Column(SmallInteger, nullable=False, default=2)
    is_active: bool = Column(Boolean, nullable=False, default=True)
```

### 2.2 Modificaciones a modelos existentes

**`backend/models/supply_chain.py`**
- `IngredientSubstitute`: eliminar `affects_regions = Column(ARRAY(Integer))`
- Agregar modelo `IngredientSubstituteRegion` (junction table)
- Todos los `Integer` PK → `BigInteger` con `Identity(always=True)` en tablas de alto crecimiento
- `Numeric` sin precisión → `Numeric(14, 4)` (precios) o `Numeric(14, 6)` (cantidades)
- Agregar `CHECK(valid_until IS NULL OR valid_until >= valid_from)` en tablas con vigencia temporal
- `StoreSupplierHistory`: agregar el EXCLUDE constraint como comentario (definido en migración)

**`backend/models/store.py`**
- `Store`: agregar `FK a currencies(code)` en `default_currency_code`
- `StoreIngredientPrice`: agregar `created_at`

**`backend/models/ingredient.py`**
- Agregar `canonical_unit`, asegurar `updated_at` con trigger referenciado

**`backend/models/product.py`**
- `Product`: `category` con `ForeignKey("categories.slug")`
- `ProductSize`: agregar UNIQUE `(product_id, size_name)`, `CHECK(is_default NOT NULL)`
- `RecipeIngredients`: agregar UNIQUE `(product_id, ingredient_id)`
- `RecipeSubRecipes`: agregar `CHECK(parent_product_id <> sub_recipe_id)`, UNIQUE `(parent, sub)`

**`backend/models/pricing.py`**
- `ProductPricing`: `currency_code` con FK a `currencies`, UNIQUE index con COALESCE
- `ProductPriceHistory`: tabla particionada → `__table_args__` con `postgresql_partition_by='RANGE (changed_at)'`
- `IngredientPriceHistory`: ídem particionada
- `RecipeCostSnapshot`: ídem particionada

**`backend/models/competitor.py`**
- `CompetitorProduct`: tabla particionada por `scraped_at`
- `ProductCompetitorMatch`: FK compuesta a `competitor_products(id, scraped_at)`, agregar `competitor_product_scraped_at`

### 2.3 Nuevo archivo: `backend/models/ingredient_substitute_region.py`
```python
class IngredientSubstituteRegion(Base):
    __tablename__ = "ingredient_substitute_regions"
    substitute_id: int = Column(BigInteger, ForeignKey("ingredient_substitutes.id", ondelete="CASCADE"), primary_key=True)
    region_id: int = Column(BigInteger, ForeignKey("regions.id", ondelete="CASCADE"), primary_key=True)
```

### 2.4 Actualizar `backend/models/__init__.py`
Exportar `Currency`, `IngredientSubstituteRegion`.

---

## Etapa 3 — Schemas Pydantic

**Objetivo**: actualizar los schemas de validación/serialización para los nuevos tipos y tablas.

### Archivos a modificar

| Archivo | Cambios |
|---------|---------|
| `backend/schemas/supply_chain.py` | Eliminar `affects_regions` de `IngredientSubstituteCreate/Read`. Agregar schemas `IngredientSubstituteRegionCreate`, `IngredientSubstituteRegionRead` |
| `backend/schemas/ingredient.py` | Agregar `canonical_unit: Optional[str]` |
| `backend/schemas/store.py` | `default_currency_code` con validador de 3 chars mayúsculas |
| `backend/schemas/pricing.py` | `currency_code` validado contra `Literal['COP', 'USD', 'EUR']` o FK dinámica |
| `backend/schemas/product.py` | `category` opcional con validación de slug format |

### Nuevo archivo: `backend/schemas/currency.py`
```python
class CurrencyRead(BaseModel):
    code: str
    name: str
    minor_unit: int
    is_active: bool
```

---

## Etapa 4 — Routers

**Objetivo**: actualizar los routers existentes y crear los nuevos.

### Nuevo archivo: `backend/routers/currencies.py`
- `GET /api/currencies` → lista activas (seed: COP, USD, EUR)

### Modificaciones a routers existentes

| Router | Cambio |
|--------|--------|
| `supply_chain.py` | Endpoints de `ingredient_substitutes` deben usar `ingredient_substitute_regions` en lugar de `affects_regions`. Agregar endpoints CRUD para `IngredientSubstituteRegion` |
| `costs.py` | Actualizar queries para usar `v_current_ingredient_price` (vista) |
| `pricing.py` | Asegurar que `currency_code` se valida contra `currencies` |
| `stores.py` | `default_currency_code` debe ser validado |

### Modificaciones a templates

| Template | Cambio |
|----------|--------|
| `supply_chain/routes/detail.html` | Eliminar campo `affects_regions`, mostrar `ingredient_substitute_regions` como tabla |
| `stores/detail.html` | Mostrar `default_currency_code` con descripción de la moneda |
| Cualquier template que muestre precios | Mostrar símbolo de moneda desde `currencies` |

### Registrar nuevo router en `backend/main.py`
```python
from backend.routers import currencies
app.include_router(currencies.router)
```

---

## Etapa 5 — Servicios

### `backend/services/cost_calculator.py`
- Actualizar consultas de precios para usar `v_current_ingredient_price` (vista) en lugar de `ingredient_price_history` directamente
- Actualizar cálculo de modificadores para usar `v_product_modifier_cost`
- Asegurar que `RecipeCostSnapshot` usa `currency_code` de `stores.default_currency_code`

### `backend/services/pricing_engine.py`
- Actualizar para usar `v_product_effective_price`
- Asegurar compatibilidad con `product_pricing` que ahora tiene FK a `currencies`

### `backend/services/report_generator.py`
- Actualizar queries que acceden a historiales (ahora particionados)
- Los queries a `product_price_history` e `ingredient_price_history` siguen funcionando igual (PostgreSQL enruta automáticamente), pero pueden necesitar `ORDER BY changed_at DESC` actualizado

---

## Etapa 6 — Tests

### `backend/tests/conftest.py`
Actualizar fixtures:
- `Currency` seed data en fixtures
- `Store` con `default_currency_code='COP'` explícito con FK
- Eliminar uso de `affects_regions` en fixtures de `IngredientSubstitute`
- Agregar fixtures para `IngredientSubstituteRegion`

### Tests a actualizar

| Archivo | Cambios necesarios |
|---------|-------------------|
| `test_supply_chain_schemas.py` | Actualizar tests de `IngredientSubstitute` para nueva estructura |
| `test_supply_chain_api.py` | Actualizar tests de substitutos, agregar tests para `currencies` |
| `test_stores_ui_fase_c.py` | Verificar que `region_id` y `default_currency_code` siguen funcionando |
| `test_cost_calculator.py` | Actualizar mocks para nuevo schema |

### Tests nuevos a crear
- `test_currencies.py`: CRUD básico de currencies
- `test_ingredient_substitute_regions.py`: junction table CRUD y FK constraints
- `test_schema_constraints.py`: verificar que los CHECK/UNIQUE/EXCLUDE se disparan correctamente

---

## Checklist de implementación

### Etapa 0
- [ ] Crear rama `refactor/greenfield-schema`
- [ ] Reset schema Supabase
- [ ] Archivar migraciones antiguas

### Etapa 1 — Migración
- [ ] Crear `0001_initial_schema.py` con DDL completo
- [ ] Ejecutar `alembic upgrade head` contra Supabase
- [ ] Verificar con queries del CLAUDE.md sección 13 (post-implementación)

### Etapa 2 — Modelos
- [ ] `backend/models/currency.py` creado
- [ ] `backend/models/ingredient_substitute_region.py` creado
- [ ] `IngredientSubstitute.affects_regions` eliminado
- [ ] `ProductPriceHistory` particionada
- [ ] `IngredientPriceHistory` particionada
- [ ] `RecipeCostSnapshot` particionada
- [ ] `CompetitorProduct` particionada + FK compuesta en `ProductCompetitorMatch`
- [ ] Todos los ON DELETE/ON UPDATE en FKs
- [ ] Todos los CHECK/UNIQUE faltantes
- [ ] `backend/models/__init__.py` actualizado

### Etapa 3 — Schemas
- [ ] `backend/schemas/currency.py` creado
- [ ] `affects_regions` eliminado de schemas de supply chain
- [ ] Schemas de `IngredientSubstituteRegion` creados
- [ ] Validadores de tipos numéricos actualizados

### Etapa 4 — Routers
- [ ] `backend/routers/currencies.py` creado
- [ ] `backend/main.py` registra nuevo router
- [ ] Router de substitutos actualizado
- [ ] Templates actualizados

### Etapa 5 — Servicios
- [ ] `cost_calculator.py` usa vistas
- [ ] `pricing_engine.py` actualizado
- [ ] `report_generator.py` compatible con particiones

### Etapa 6 — Tests
- [ ] `conftest.py` actualizado
- [ ] Tests existentes actualizados
- [ ] Tests nuevos para currencies y junction table
- [ ] Suite completa pasa: `pytest backend/tests/`

---

## Decisiones de diseño que NO cambian

- SQLAlchemy Core (no ORM relationships) — los modelos no usan `relationship()` ni lazy loading
- FastAPI + Jinja2/HTMX para la UI — no hay cambio de framework
- Alembic para gestión de migraciones
- La función `fn_resolve_supply_route` del CLAUDE.md sigue siendo la fuente de verdad para lógica de rutas
- El patrón `valid_until + INSERT` (nunca UPDATE de datos de negocio) se mantiene

---

## Referencia rápida de tipos en modelos

| Dominio SQL | SQLAlchemy Python |
|-------------|-------------------|
| `price_amount` (`numeric(14,4) >= 0`) | `Numeric(14, 4)` + `CheckConstraint('col >= 0')` |
| `quantity_amount` (`numeric(14,6) >= 0`) | `Numeric(14, 6)` + `CheckConstraint('col >= 0')` |
| `pct_amount` (`numeric(6,3)`) | `Numeric(6, 3)` |
| `iso_country` (`char(2)`) | `String(2)` + `CheckConstraint("col ~ '^[A-Z]{2}$'")` |
| `bigint GENERATED ALWAYS AS IDENTITY` | `BigInteger, Identity(always=True), primary_key=True` |
| Tabla particionada | `__table_args__ = {..., 'postgresql_partition_by': 'RANGE (col)'}` |
| FK con ON DELETE CASCADE | `ForeignKey("tabla.id", ondelete="CASCADE")` |
| FK con ON DELETE RESTRICT | `ForeignKey("tabla.id", ondelete="RESTRICT")` |
| FK con ON DELETE SET NULL | `ForeignKey("tabla.id", ondelete="SET NULL")` |
| FK con ON UPDATE CASCADE | `ForeignKey("tabla.id", onupdate="CASCADE")` |
