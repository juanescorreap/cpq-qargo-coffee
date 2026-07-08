# Diagnóstico de gaps de precio — Edinburgo (TX)

> Resultados del diagnóstico read-only definido en `prompt_diagnostico_edinburgo.md`.
> Ejecutado 2026-07-07 contra la base de producción (Supabase). **No se modificó
> ningún dato.** Todas las consultas fueron `SELECT` / función `STABLE`.

---

## Resumen ejecutivo

| Métrica | Valor |
|---|---|
| Tienda | id **519** · `Edinburg` · code `7-ED-TX` · region 930 · USD |
| Ingredientes en recetas activas | **164** |
| A — precio vía ruta (máx. precisión) | **12** (7.3%) |
| B — precio vía catálogo, sin ruta | **7** (4.3%) |
| C — precio fallback (Excel), sin sync | **145** (88.4%) |
| D — sin precio ($0) | **0** (0%) |
| Productos con costo subestimado por D | **0** |
| Pendientes de mapeo creados por sync de Edinburgo | **0** |

**Conclusión de una línea:** Edinburgo **puede correr el pricing engine hoy sin riesgo
de costo $0** — los 164 ingredientes tienen precio. El riesgo no es *completitud* sino
*confiabilidad*: el 88% (C) usa precio del Excel de carga sin refresh por el sync.

---

## 1 · Store info + sync status

| Campo | Valor |
|---|---|
| store_id | 519 |
| name | Edinburg |
| code | 7-ED-TX |
| region_id | 930 |
| default_currency_code | USD |
| catalog_store_id | 13 (mapeado en `store_catalog_mapping`) |

**Último sync** (`catalog_sync_log` id 13):

| started_at | status | fetched | matched | created | updated | skipped | error |
|---|---|---|---|---|---|---|---|
| 2026-07-07 16:12 UTC | `success` | 139 | 135 | **0** | 23 | 4 | 0 |

El sync de Edinburgo **no creó ingredientes nuevos** — todos los items hicieron match
(SKU/fuzzy) contra el canónico existente. Por eso no aporta al backlog de 236 pending.

---

## 2 · Total ingredientes en recetas activas

**164** ingredientes distintos usados por productos `is_active=true`.
Todos con `purchase_price` no-nulo y ≠ 0 (0 nulos, 0 en cero) → estado D imposible por
definición de datos actual.

---

## 3 · Tabla de estados A/B/C/D

Clasificación con prioridad **A > B > C > D**:

- **A — PRECIO_VIA_RUTA**: `fn_resolve_supply_route(ing, 519)` devuelve ruta activa
  con `supply_route_prices` vigente (`valid_until IS NULL`). El motor usa este precio.
- **B — PRECIO_VIA_CATALOGO**: apareció en el sync de Edinburgo (store 519) con
  `action_taken IN ('created','updated')` y tiene `purchase_price`, pero **sin** ruta
  activa. El motor usa `purchase_price` como fallback.
- **C — PRECIO_FALLBACK**: tiene `purchase_price` (Excel de carga) pero **no** llegó
  precio del sync de Edinburgo. Puede estar desactualizado.
- **D — SIN_PRECIO**: ni ruta ni `purchase_price` → motor devuelve $0.

| Estado | Count | % | El motor usa |
|---|---|---|---|
| A | 12 | 7.3% | precio de ruta (trazable) |
| B | 7 | 4.3% | `purchase_price` (fresco, sin traza de ruta) |
| C | 145 | 88.4% | `purchase_price` (posible desactualizado) |
| D | 0 | 0% | — |
| **Total** | **164** | 100% | |

### Estado A (12) — precio vía ruta

Todas resuelven por `store_override` (excepción de tienda), priority 1. Precio mostrado
= `qargo_price` vigente.

| id | Ingrediente | route | scope | precio ruta |
|---|---|---|---|---|
| 75 | All Butter Croissant | 33 | store_override | 50.87 USD |
| 74 | Chocolate Croissant | 25 | store_override | 61.51 USD |
| 104 | Chocolate Eclair | 100 | store_override | 28.49 USD |
| 125 | Chocolate Ganache Cheesecake | 12 | store_override | 42.22 USD |
| 114 | Chocolate Temptation Cake | 17 | store_override | 42.26 USD |
| 123 | Dulce de Leche Cheesecake | 83 | store_override | 39.38 USD |
| 108 | Macarons French | 111 | store_override | 153.50 USD |
| 122 | Pistachio Cheesecake | 89 | store_override | 40.12 USD |
| 113 | Red Velvet Cake | 80 | store_override | 39.33 USD |
| 124 | Strawberry Cheesecake | 2 | store_override | 37.44 USD |
| 109 | Tiramisu | 59 | store_override | 34.27 USD |
| 110 | Tiramisu With Ladyfingers | 58 | store_override | 46.14 USD |

> Nota: todos los A son productos de repostería de reventa (finished goods). El
> `purchase_price` en `ingredients` a veces es una porción unitaria (ej. Cheesecake
> 2.64) mientras la ruta cotiza el empaque de compra (ej. 42.22/case). El motor debe
> usar la ruta + su `price_per_unit` para estos.

### Estado B (7) — precio de catálogo, sin ruta

| id | Ingrediente | purchase_price | purchase_unit |
|---|---|---|---|
| 1 | Milk | 5.20 | 1 gal |
| 3 | Coconut Milk | 36.95 | Case 12 × 32 oz |
| 21 | Coconut Syrup | 6.75 | 1 L |
| 23 | Dragon Fruit Syrup | 6.75 | 1 L |
| 32 | Strawberry Fruit Puree | 15.00 | 1 L |
| 52 | Water | 19.00 | 1 gal |
| 68 | Focaccia | 63.96 | Pack 5 units |

Estos tienen el **precio más fresco disponible** (viene del sync real de Edinburgo)
pero sin ruta → sin trazabilidad. Subirlos B→A (creándoles `supply_route` +
`supply_route_prices`) es la mejora de calidad de mayor impacto — ver deuda técnica en
`PENDING_ITEMS.md` (`_create_ingredient`).

### Estado C (145) — fallback Excel

Precio existe pero sin refresh del sync de Edinburgo. Lista completa en el
[Apéndice A](#apéndice-a--estado-c-completo-145).

### Estado D (0)

Vacío. Ningún ingrediente de receta activa carece de precio.

---

## 4 · Productos afectados por ingredientes D

**Ninguno.** D = 0 → el motor no devuelve $0 para ningún producto de Edinburgo. No hay
costo subestimado por falta de precio.

### Nota de priorización — impacto de B (proxy del análisis de impacto)

Como D está vacío, el análisis de impacto se traslada al conjunto B (los precios sin
ruta, los de mayor incertidumbre de trazabilidad). Nº de productos activos que usan
cada ingrediente B:

| Ingrediente B | # productos | Productos |
|---|---|---|
| **Milk** | **32** | Caffe Latte, Cappuccino, Cortado, Flat White, Mocha, Hot Chocolate, todos los Iced Latte/Matcha/Mocha, Boba Tea, Frappe, Gelato, Smoothies… |
| Water | 8 | Caffe Americano, Hot Tea, Ice Cubes, Iced Americano, Latte Matcha, Boba Tea Pure Matcha, Iced Latte Matcha, Iced Matcha Coconut |
| Focaccia | 6 | Pesto Pomodoro, Prosciutto Royale, Salmone Fresco, Turkey Bacon al Fresco / Classico / Formaggio (sandwiches) |
| Coconut Syrup | 4 | Boba Tea Coconutty, Cold Foam Coconut, Iced Latte Coconut, Iced Matcha Coconut |
| Coconut Milk | 3 | Cold Brew Coco Choc, Cold Brew Coco Choc Nitro, Cold Foam Coconut |
| Dragon Fruit Syrup | 1 | Soda Mother of Dragons |
| Strawberry Fruit Puree | 1 | Frappe Italian Strawberry |

**Milk (id 1)** es el ingrediente crítico: 32 productos dependen de él. Su precio es
fresco (sync), pero darle ruta lo blinda para trazabilidad y auditoría.

---

## 5 · Pendientes de mapeo de Edinburgo

| Métrica | Valor |
|---|---|
| Ingredientes `created` por el sync de Edinburgo | **0** |
| De esos, presentes en recetas activas | **0** |

Consistente con `items_created=0` del sync. Los 236 pending globales provienen de otras
tiendas y **no impactan directamente** las recetas activas de Edinburgo. Mapearlos no
mejora el cálculo de costos de esta tienda.

---

## Lectura para el diseño del pricing engine

1. **No hay bloqueo por $0.** Los 164 ingredientes tienen precio; el motor corre hoy.
2. **Riesgo real = C (88%):** precio del Excel de carga, sin refresh. No es error, es
   confiabilidad / posible staleness. Palanca: forzar sync que cubra estos SKUs.
3. **Solo 12 (A)** tienen precio con trazabilidad de ruta. Subir cobertura A es la
   palanca de *calidad*, no de completitud.
4. **B (7)** tienen precio fresco de la API pero sin ruta → la deuda de
   `_create_ingredient` (`PENDING_ITEMS.md`) aplica: darles `supply_route` +
   `ingredient_supplier_ref` + `supply_route_prices` los sube B→A.
5. **Mapeo de pending no ayuda a Edinburgo** (0 creados aquí).

---

## Nota metodológica

- El spec (Paso 3-B) pide `action_taken IN ('created','price_updated')`. El valor real
  en producción es **`'updated'`** — `'price_updated'` no existe. Se usó
  `('created','updated')`. Mismo ajuste ya documentado para `'ingredient_created'` →
  `'created'`.
- Estado A resuelto con `fn_resolve_supply_route(ingredient_id, 519)` (la fuente única
  de verdad, Principio P6 de CLAUDE.md) join a `supply_route_prices` vigente.
- Clasificación por prioridad A > B > C > D: un ingrediente con ruta se cuenta como A
  aunque también aparezca en el sync.

---

## Apéndice A — Estado C completo (145)

`id | nombre | purchase_price | purchase_unit`

```
132 | A Siciliana Aranciata | 2.50 | unit
133 | A Siciliana Limonata | 2.50 | unit
134 | A Siciliana Mandarino | 2.50 | unit
135 | A Siciliana Melograno | 2.50 | unit
127 | Acqua Panna | 1.75 | unit
76  | Almond Croissant | 1.2991 | unit
2   | Almond Milk | 35.00 | Case 12 × 32 oz
84  | Apple Butter Danish | 1.0049 | unit
137 | Apple Juice | 3.00 | unit
91  | Apple Square Danish | 1.0049 | unit
144 | Apple Tropicana Juice | 5.50 | unit
54  | Arugula | 19.18 | Bag 4 lb
57  | Avocado | 33.75 | unit
37  | Balsamic Vinegar | 21.1412 | 5 L
102 | Banana Nut Muffin | 129.99 | Package 25 lb
56  | Basil | 27.06 | Container 1 lb
94  | Berries and Cream Twist Danish | 1.0049 | unit
138 | Black River Pear Juice | 2.50 | unit
87  | Blueberry Cheesecake Danish | 1.0049 | unit
98  | Blueberry Scone | 1.94 | unit
67  | Bread | 24.94 | unit
13  | Brewed Coffee | 7.93 | 1.5 gal
163 | Brewed Tea | 0.245 | 3 gal
126 | Brownie Cheesecake | 2.25 | unit
136 | Bundaberg Ginger Beer | 1.50 | unit
103 | Cacao Muffin | 129.99 | Package 25 lb
117 | Cake Torta Nocciola | 3.977 | unit
59  | Capers | 18.99 | Package 2 lb
116 | Cappuccino Cake | 3.535 | unit
164 | Cappuccino Foam | 4.87 | 1 gal
115 | Carrot Cake | 46.69 | unit
15  | Chai Tea Concentrate | 6.75 | 1 L
5   | Cheese Cheddar Sliced | 0.1935 | unit
82  | Cheese Danish | 1.0049 | unit
6   | Cheese Mozzarella Sliced | 0.1737 | unit
96  | Cheese Square Danish | 1.0049 | unit
86  | Cherry Greek Yogurt Danish | 1.0049 | unit
48  | Chia Seeds | 37.49 | Bag 5 lb
65  | Chicken Breast Grilled | 16.00 | lb
99  | Chocolate Chip Cookie | 129.99 | Package 25 lb
100 | Chocolate Chip Muffin | 129.99 | Package 25 lb
118 | Chocolate Fondant Cake | 3.0606 | unit
10  | Chocolate Gelato | 0.5416 | unit
41  | Chocolate Powder | 22.99 | Canister 3 lb
33  | Chocolate Sauce | 23.49 | Bottle 64 oz
73  | Cinnamon Raisin Bagel | 0.9069 | unit
97  | Cinnamon Roll | 10.19 | unit
53  | Club Soda | 17.95 | 1 L
11  | Coconut Flakes | 8.39 | Bag 10 oz
106 | Coffee Eclair | 42.54 | Case x 72
12  | Cold Brew | 70.40 | 1 gal
165 | Cold Foam Coconut | 40.69 | Case 12 × 32 oz
49  | Cooking Spray | 5.79 | Can 17 oz
31  | Cream Base | 19.99 | 0.5 gal
4   | Cream Cheese | 22.13 | 100 units
7   | Crumbled Blue Cheese | 22.50 | Container 5 lb
46  | Crushed Oreo | 6.99 | 1 lb
80  | Doughnut Croissant | 1.2991 | unit
58  | Dried Oregano | 11.49 | Case 1.5 lb
153 | Energy Drink Celsius Artic Vibe | 1.20 | unit
154 | Energy Drink Celsius Kiwi Giava | 1.20 | unit
149 | Energy Drink Celsius Mango Lemonade | 1.20 | unit
147 | Energy Drink Celsius Orange | 28.75 | unit
150 | Energy Drink Celsius Peach Vibe | 1.20 | unit
151 | Energy Drink Celsius Tropical Vibe | 1.20 | unit
148 | Energy Drink Celsius Watermelon | 1.20 | unit
152 | Energy Drink Celsius Wildberry | 1.20 | unit
155 | Energy Drink Celsuis Strawberry Lemonade | 1.20 | unit
158 | Espresso | 188.16 | Bag 5 lb
72  | Everything Bagel | 0.6644 | unit
139 | Five Star Apple Juice | 3.00 | unit
140 | Five Star Cranberry Juice | 3.00 | unit
141 | Five Star Orange Juice | 3.00 | unit
142 | Five Star Pineapple Juice | 3.00 | unit
157 | Flavored Water Mango Pineapple Splash | 1.50 | unit
78  | Glazed Apricot Croissant | 1.2991 | unit
8   | Greek Yogurt | 94.66 | Container 32 oz
17  | Green Tea | 32.40 | 10 oz
90  | Guava Danish | 1.0049 | unit
22  | Hazelnut Syrup | 34.47 | 1 L
47  | Honey Oat Granola | 19.49 | Case 4 × 12 oz
159 | Ice Cubes | 0.01 | 1 gal
43  | Icing Sugar | 22.49 | Box 10 lb
88  | Leek and Parmesan Danish | 1.0049 | unit
89  | Lemon Danish | 1.0049 | unit
38  | Lemon Juice | 32.25 | 1.5 gal
27  | Mango Passion Fruit Fusion | 19.00 | 0.5 gal
83  | Maple Pecan Danish | 1.0049 | unit
18  | Matcha | 377.50 | Bag 1 kg
36  | Mayonnaise | 58.00 | Case 4 × 4 gal
35  | Mustard | 29.00 | Case 420 oz
146 | Natalie's Pineapple Cucumber and Celery | 5.00 | unit
14  | Nitro Cold Brew | 36.00 | 1 gal
39  | Olive Oil | 108.49 | Case 4 X 3 L
30  | Orange Creamsicle Fruit Fusion | 19.00 | 0.5 gal
50  | Orange Food Coloring | 69.95 | Bottle 8 oz
143 | Orange Tropicana Juice | 5.50 | unit
161 | Pepper | 25.00 | Bag 5 lb
34  | Pesto | 85.00 | Canister 38 lb
28  | Pina Colada Fruit Fusion | 19.00 | 0.5 gal
69  | Pinsa | 81.39 | unit
70  | Pinsa Margherita | 60.55 | unit
77  | Pistachio Croissant | 1.2991 | unit
101 | Pistachio Muffin | 129.99 | Package 25 lb
71  | Plain Bagel | 0.6444 | unit
66  | Proscuitto | 1.1726 | unit
95  | Raspberry Cream Cheese Danish | 1.0049 | unit
120 | Red Velvet Mini Gluten Free Cake | 36.99 | unit
121 | Ricotta and Pistachio Cake | 2.9969 | unit
20  | Rose Syrup | 11.99 | 1 L
162 | Salt | 15.00 | Case 25 lb
128 | San Pellegrino | 24.99 | unit
129 | San Pellegrino Aranciata | 0.99 | unit
130 | San Pellegrino Ciao Lime | 0.99 | unit
131 | San Pellegrino Limonata | 24.99 | unit
61  | Scrambled Egg Patty | 0.346 | unit
51  | Seasoning Mix | 3.5163 | Bag 10 oz
145 | Simply Orange Juice | 5.50 | unit
60  | Sliced Pickles | 8.95 | 1 gal
55  | Sliced Tomato | 1.30 | lb
64  | Smoked Salmon | 66.50 | lb
81  | Spinach and Feta Danish | 1.0049 | unit
79  | Spinach Kale and Cheese Croissant | 1.2991 | unit
93  | Strawberry Danish | 1.0049 | unit
25  | Strawberry Fruit Fusion | 19.00 | 0.5 gal
111 | Stroopwafel | 1.25 | unit
45  | Tapioca Pearls | 14.99 | Case 3 lb
42  | Taro Powder | 18.49 | Bag 2.2 lb
107 | Tart Mixed Berry | 2.67 | unit
16  | Tea Bag | 0.2446 | 3 gal
92  | Tomato and Olive Danish | 1.0049 | unit
119 | Tres Leches Cake | 2.2227 | unit
62  | Turkey Bacon | 0.196 | unit
63  | Turkey Sausage Patty | 0.48 | unit
105 | Vanilla Eclair | 28.49 | Case x 72
9   | Vanilla Gelato | 0.5416 | unit
40  | Vanilla Powder | 26.99 | Canister 3 lb
19  | Vanilla Syrup | 222.97 | 1 L
112 | Waffles Birthday Cake | 1.20 | unit
156 | Water Bottle Smart | 0.95 | unit
29  | Watermelon Fruit Fusion | 19.00 | 0.5 gal
44  | Whey Protein | 64.99 | Container 5 lb
160 | Whipped Cream | 9.50 | Can 14 oz
24  | White Lotus | 31.95 | 0.5 gal
```

> Nota de datos: varios C tienen `purchase_price` sospechoso de estar en unidad
> inconsistente (ej. Muffins a 129.99/Package 25 lb vs. otros por unit; Ice Cubes 0.01)
> — no es objeto de este diagnóstico pero conviene validarlo al calibrar el motor.
