# Rediseño del Motor de Cálculo de Costos — Qargo Coffee

> Propuesta de arquitectura (Principal Engineer) para `backend/services/cost_calculator.py`
> + `pricing_engine.py`. Objetivo: baja latencia, batch a millones de combinaciones,
> tolerante a fallos, con trazabilidad. NO implementado — es diseño.

## Principios

1. **Prefetch → Compute puro → Persist.** Cero I/O en el loop de cálculo.
2. **Memoización del BOM** (DAG) → cada sub-receta se valúa una vez (O(V+E)).
3. **Función pura** (precios, recetas) → paralelizable por proceso.
4. **Snapshot inmutable** por cálculo → trazabilidad/lineage.
5. **Incremental event-driven** además del batch.

---

## Fase 1 — Precarga (pocas queries bulk, no N+1)

```python
@dataclass(frozen=True)
class CalcContext:
    """Snapshot inmutable de todos los datos necesarios. Sin sesión DB dentro."""
    recipe_lines: dict[int, list[RecipeLine]]      # product_id -> líneas
    sub_recipes:  dict[int, list[SubRef]]          # parent_id -> [(sub_id, qty, scales)]
    packaging:    dict[int, list[PkgLine]]         # size_id -> empaque
    sizes:        dict[int, list[SizeInfo]]         # product_id -> tallas
    ingredients:  dict[int, IngredientInfo]        # id -> conv_factor, yield, current_price
    unit_conv:    dict[tuple[int, int], Decimal]   # (ingredient_id, recipe_unit_id) -> factor
    unit_price:   dict[int, Decimal]               # ingredient_id -> precio unitario resuelto
    labor:        dict[int, Decimal]               # product_id -> prep_min * cost_min
    formula_version: str

def load_context(db, store_id: int | None, product_ids: list[int] | None = None) -> CalcContext:
    # 1 query por tabla (filtradas por product_ids si es incremental):
    #   recipe_ingredients, recipe_sub_recipes, size_packaging, product_sizes,
    #   ingredients, ingredient_recipe_unit_conversions, products(labor)
    # PRECIO: resolver TODO en UNA query set-based en vez de fn por-fila.
    #   Para un store: una sola consulta lateral que aplica la precedencia
    #   local→ruta→catálogo para todos los ingredientes a la vez.
    ...
```

**Clave de rendimiento:** el precio se resuelve **set-based** una sola vez (no `fn_ingredient_unit_cost` por ingrediente). Una CTE/lateral que para cada `ingredient_id` calcule `COALESCE(local_vigente, qargo_ruta, current_price, purchase_price)`.

---

## Fase 2 — Cálculo puro con memoización del BOM

```python
def base_recipe_cost(ctx: CalcContext, product_id: int, memo: dict[int, Decimal],
                     visiting: set[int]) -> Decimal:
    """Costo base (talla=1) de un producto. Memoizado: cada nodo del DAG una vez.
    'visiting' detecta ciclos en O(1) (defensa en profundidad; la BD ya los impide)."""
    if product_id in memo:
        return memo[product_id]
    if product_id in visiting:
        raise CycleError(product_id)
    visiting.add(product_id)

    total = Decimal(0)

    # Ingredientes directos (scale=1 aquí; la talla se aplica en cost_for_size)
    for line in ctx.recipe_lines.get(product_id, []):
        total += _line_cost(ctx, line, scale=Decimal(1))

    # Sub-recetas: recursión memoizada (post-orden) → O(V+E)
    for sub in ctx.sub_recipes.get(product_id, []):
        sub_unit = base_recipe_cost(ctx, sub.sub_id, memo, visiting)
        total += sub_unit * sub.qty            # scale=1 en base

    total += ctx.labor.get(product_id, Decimal(0))

    visiting.discard(product_id)
    memo[product_id] = total
    return total

def _line_cost(ctx, line, scale: Decimal) -> Decimal:
    ing = ctx.ingredients[line.ingredient_id]
    if not ing.conversion_factor:
        return Decimal(0)
    qty = line.quantity
    if line.recipe_unit_id is not None:
        qty *= ctx.unit_conv[(line.ingredient_id, line.recipe_unit_id)]
    if line.scales_with_size:
        qty *= scale
    if ing.yield_percentage and ing.yield_percentage > 0:
        qty /= ing.yield_percentage
    if 0 < line.process_yield_loss < 100:
        qty /= (line.process_yield_loss / 100)
    unit_cost = ctx.unit_price[line.ingredient_id] / ing.conversion_factor
    return unit_cost * qty
```

**Para cada (producto, talla):** no recalcular la receta — calcular el costo base una vez y aplicar `scale_factor` solo a las líneas con `scales_with_size`. Separar costo "fijo" vs "escalable" al construir el memo:

```python
@dataclass
class BaseCost:
    fixed: Decimal      # líneas + sub-recetas que NO escalan + labor
    scalable: Decimal   # líneas + sub-recetas que SÍ escalan (a scale=1)

def cost_for_size(base: BaseCost, scale: Decimal, currency_minor: int) -> Decimal:
    raw = base.fixed + base.scalable * scale
    return raw.quantize(Decimal(10) ** -currency_minor)   # redondeo por moneda
```

Así `calculate_all_prices` es O(V+E) para los costos base + O(P·S) para escalar — todo en memoria, sin DB.

---

## Fase 3 — Persistencia + trazabilidad

```python
def persist(db, results: list[ProductSizeCost], ctx: CalcContext, triggered_by: str):
    # Bulk upsert product_pricing; bulk insert product_price_history (solo cambios).
    # SNAPSHOT inmutable por resultado (hoy la tabla existe y NO se usa):
    for r in results:
        db.add(RecipeCostSnapshot(
            product_id=r.product_id, store_id=r.store_id,
            base_cost=r.base, effective_cost=r.effective,
            currency_code=r.currency, has_substitutes=r.has_subs,
            snapshot_detail=r.breakdown_json,        # desglose línea-a-línea
            triggered_by=triggered_by,               # 'batch'|'price_change'|'manual'
        ))
    # breakdown_json incluye: por ingrediente {id, qty, unit_price, line_cost,
    # price_source}, formula_version, y los valid_from de precios usados → lineage.
```

---

## Fase 4 — Concurrencia (batch)

```python
def recompute_all(store_id: int | None, workers: int = 8):
    product_ids = all_active_product_ids()           # 1 query
    # Particionar y paralelizar por proceso (cada worker abre su sesión a réplica):
    chunks = partition(product_ids, workers)
    with ProcessPoolExecutor(workers) as pool:
        for chunk in chunks:
            pool.submit(_worker, chunk, store_id)    # cada worker: load_context+compute+persist

def _worker(chunk, store_id):
    db = SessionLocal()                              # sesión propia por proceso
    try:
        ctx = load_context(db, store_id, product_ids=chunk_plus_subrecipes(chunk))
        memo = {}
        results = [cost_for_size(...) for p in chunk for s in ctx.sizes[p]]
        persist(db, results, ctx, triggered_by="batch")
        db.commit()                                  # commit POR CHUNK (checkpoint)
    except Exception:
        db.rollback(); enqueue_retry(chunk)          # reintento idempotente del chunk
    finally:
        db.close()
```

- **Checkpoint por chunk** → reanudable; un fallo solo reintenta su chunk.
- **Idempotente:** upsert por clave + snapshots append-only (cada corrida = nueva foto).

---

## Fase 5 — Incremental event-driven

```python
# Trigger/cola: al insertar ingredient_price_history o cambiar ruta/sustituto,
# invalidar SOLO los productos afectados vía grafo BOM inverso.
def on_price_change(ingredient_id: int):
    affected = reverse_bom_closure(ingredient_id)    # productos que usan el ingrediente
    recompute_subset(affected, triggered_by="price_change")
```

`reverse_bom_closure` = cierre transitivo sobre `recipe_ingredients` ∪ `recipe_sub_recipes` (CTE recursiva).

---

## Resumen de ganancias

| Aspecto | Actual | Rediseño |
|---|---|---|
| Sub-recetas | reexpansión exponencial | memo O(V+E) |
| Queries por cálculo | N+1 (×recursión) | bulk: O(1) por tabla |
| Precio | `fn` por ingrediente (round-trip) | set-based, 1 query |
| Batch P×S | O(P·S·costo) con I/O | O(V+E + P·S) CPU puro |
| Paralelismo | ninguno | por proceso, checkpoint por chunk |
| Trazabilidad | snapshots ociosos | snapshot + formula_version + lineage |
| Redondeo | fijo 2 dec | por `currencies.minor_unit` |
| Reanudable | no | sí (chunk + retry idempotente) |

## Decisiones de estructuras de datos
- `dict` (HashMap) para todos los mapas de precarga → lookup O(1) en el hot loop.
- `memo: dict[int, BaseCost]` → cada nodo BOM una vez.
- `visiting: set[int]` → detección de ciclo O(1) (defensa; BD ya lo impide con trigger V3-8).
- `@dataclass(frozen=True)` para el contexto → inmutable, seguro de compartir entre hilos de lectura.
- `Decimal` en todo el cálculo; `quantize` solo al final, según moneda.
