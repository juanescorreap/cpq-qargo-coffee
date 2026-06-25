# Crosswalk — Decisiones Humanas Requeridas

Para cada fila indica una de:
  A) canonical_name = <nombre exacto de la lista> → mapear
  B) unmatched → no es ingrediente de receta o no existe aún
  C) nuevo → agregar el ingrediente a ingredients.xlsx primero

Nombres canónicos disponibles (165 ingredientes en raw/ingredients.xlsx):

  [A] A Siciliana Aranciata | A Siciliana Limonata | A Siciliana Mandarino | A Siciliana Melograno | Acqua Panna | Almond Milk | Arugula | Avocado
  [B] Bagel Cinnamon Raisin | Bagel Everything | Bagel Plain | Balsamic Vinegar | Basil | Blue Raspberry Fruit Fusion | Bread | Brewed Coffee | Brewed Tea | Bundaberg Ginger Beer
  [C] Cake Cappuccino | Cake Carrot | Cake Chocolate Fondant | Cake Chocolate Temptation | Cake Red Velvet | Cake Red Velvet Mini Gluten Free | Cake Ricotta and Pistachio | Cake Torta Nocciola | Cake Tres Leches | Capers | Cappuccino Foam | Chai Tea Concentrate | Cheese Blue Crumbled | Cheese Cheddar Sliced | Cheese Mozzarella Sliced | Cheeseacake Strawberry | Cheesecake Brownie | Cheesecake Chocolate Ganache | Cheesecake Dulce de Leche | Cheesecake Pistachio | Chia Seeds | Chicken Breast Grilled | Chocolate Gelato | Chocolate Powder | Chocolate Sauce | Cinnamon Roll | Club Soda | Coconut Milk | Coconut Syrup | Cold Brew | Cold Foam Coconut | Cookie Chocolate Chip | Cooking Spray | Cream Base | Cream Cheese | Croissant All Butter | Croissant Almond | Croissant Chocolate | Croissant Doughnut | Croissant Glazed Apricot | Croissant Pistachio | Croissant Spinach, Kale and Cheese | Crushed Oreo
  [D] Danish Apple Butter | Danish Apple Square | Danish Berries and Cream Twist | Danish Blueberry Cheesecake | Danish Cheese | Danish Cheese Square | Danish Cherry Greek Yogurt | Danish Guava | Danish Leek and Parmesan | Danish Lemon | Danish Maple Pecan | Danish Raspberry Creamcheese | Danish Spinach Ricotta | Danish Spinach and Feta | Danish Strawberry | Danish Tomato and Olive | Dragon Fruit Syrup | Dried Oregano
  [E] Eclair Chocolate | Eclair Coffee | Eclair Vanilla | Energy Drink Celsius Artic Vibe | Energy Drink Celsius Kiwi Giava | Energy Drink Celsius Mango Lemonade | Energy Drink Celsius Orange | Energy Drink Celsius Peach Vibe | Energy Drink Celsius Tropical Vibe | Energy Drink Celsius Watermelon | Energy Drink Celsius Wildberry | Energy Drink Celsuis Strawberry Lemonade | Espresso
  [F] Flakes Coconut | Flavored Water Mango Pineapple Splash | Focaccia
  [G] Greek Yogurt | Green Tea
  [H] Hazelnut Syrup | Honey Oat Granola
  [I] Ice Cubes | Icing Sugar
  [J] Juice Apple | Juice Apple Tropicana | Juice Black River Pear | Juice Five Star Apple | Juice Five Star Cranberry | Juice Five Star Orange | Juice Five Star Pineapple | Juice Orange Tropicana | Juice Simply Orange
  [L] Lemon Juice
  [M] Macarons French | Mango Passion Fruit Fusion | Matcha | Mayonnaise | Milk | Muffin Banana Nut | Muffin Cacao | Muffin Chocolate Chip | Muffin Pistachio | Mustard
  [N] Natalie's Pineapple Cucumber and Celery | Nitro Cold Brew
  [O] Olive Oil | Orange Creamsicle Fruit Fusion | Orange Food Coloring
  [P] Pepper | Pesto | Pina Colada Fruit Fusion | Pinsa | Pinsa Margherita | Proscuitto
  [R] Rose Syrup
  [S] Salt | San Pellegrino | San Pellegrino Aranciata | San Pellegrino Ciao Lime | San Pellegrino Limonata | Scone Blueberry | Scrambled Egg Patty | Seasoning Mix | Sliced Pickles | Sliced Tomato | Smoked Salmon | Strawberry Fruit Fusion | Strawberry Fruit Puree | Stroopwafel
  [T] Tapioca Pearls | Taro Powder | Tart Mixed Berry | Tea Bag | Tiramisu | Tiramisu With Ladyfingers | Turkey Bacon | Turkey Sausage Patty
  [V] Vanilla Gelato | Vanilla Powder | Vanilla Syrup
  [W] Waffles Birthday Cake | Water | Water Bottle Smart | Watermelon Fruit Fusion | Whey Protein | Whipped Cream | White Lotus

---

## Los 25 NEEDS_HUMAN_DECISION

### 1. Tiramisù Toasted Almond Whole
**Problema:** Sabor almendra tostada — no existe en canónicos. ¿Es Tiramisu genérico o producto distinto?
**Opciones posibles en canónicos:** `Tiramisu`, `Tiramisu With Ladyfingers`

**Tu decisión:** c, agregarlo a ingredients.xlsx

### 2. Organic Cappuccino Cold Brew
**Problema:** Cold Brew con sabor cappuccino. ¿Mapear a Cold Brew genérico o es ingrediente distinto?
**Opciones posibles en canónicos:** `Cold Brew`, `Nitro Cold Brew`

**Tu decisión:** a, Cold Brew

### 3. Organic Double Oat Cold Brew
**Problema:** Cold Brew con oat milk. ¿Mapear a Cold Brew genérico o es ingrediente distinto?
**Opciones posibles en canónicos:** `Cold Brew`, `Nitro Cold Brew`

**Tu decisión:** a, Cold Brew

### 4. Plain Croissant
**Problema:** Croissant liso — podría ser All Butter o simplemente Croissant genérico. No existe 'Croissant Plain'.
**Opciones posibles en canónicos:** `Croissant All Butter`

**Tu decisión:** a, Croissant All Butter

### 5. Curved Croissant - Medium
**Problema:** Forma curva clásica = croissant de mantequilla. ¿Confirmar All Butter?
**Opciones posibles en canónicos:** `Croissant All Butter`

**Tu decisión:** c, Croissant All Butter Curved

### 6. Mini Croissant
**Problema:** Versión mini. ¿Qué tipo de croissant mini compra Qargo? All Butter o Almond son los más comunes en mini.
**Opciones posibles en canónicos:** `Croissant All Butter`, `Croissant Almond`

**Tu decisión:** c, Croissant All Butter Curved Mini

### 7. Vegan Croissant Vuoto
**Problema:** 'Vuoto' = vacío/sin relleno en italiano. Versión vegana sin relleno. No existe canónico vegan croissant.
**Opciones posibles:** ninguna — el ingrediente no existe en canónicos

**Tu decisión:** c, Croissant Vegan

### 8. Muffin Multigrain
**Problema:** Muffin multigrano — no existe en canónicos (solo Cacao, Banana Nut, Choc Chip, Pistachio).
**Opciones posibles en canónicos:** `Muffin Banana Nut`, `Muffin Cacao`, `Muffin Chocolate Chip`, `Muffin Pistachio`

**Tu decisión:** c, Muffin Multigrain

### 9. Raspberry Cheesecake-Style Danish
**Problema:** 'Cheesecake-style' danish de frambuesa. Dos opciones plausibles en canónicos.
**Opciones posibles en canónicos:** `Danish Raspberry Creamcheese`, `Danish Strawberry`

**Tu decisión:** a, Danish Raspberry Creamcheese

### 10. Strawberry Cheesecake-Style Danish
**Problema:** Danish estilo cheesecake de fresa. Podría ser Danish o Cheesecake.
**Opciones posibles en canónicos:** `Danish Strawberry`, `Cheeseacake Strawberry`

**Tu decisión:** a, Danish Strawberry

### 11. Strawberry Sprint
**Problema:** PreGel Sprint = pasta concentrada de sabor (≠ puré de fruta). ¿Mapear a Strawberry Fruit Puree o son diferentes?
**Opciones posibles en canónicos:** `Strawberry Fruit Puree`

**Tu decisión:** c, Strawberry Sprint

### 12. White Base Sprint
**Problema:** PreGel Sprint base blanca neutra. ¿Es la misma Cream Base de Qargo?
**Opciones posibles en canónicos:** `Cream Base`

**Tu decisión:** c, White Base Sprint

### 13. Coconut Ripieno
**Problema:** Relleno italiano de coco (≠ syrup). ¿Mapear a Coconut Syrup o es producto distinto?
**Opciones posibles en canónicos:** `Coconut Syrup`, `Flakes Coconut`

**Tu decisión:** a, Coconut Syrup

### 14. Pineapple Ripieno
**Problema:** Relleno de piña. No existe ningún ingrediente de piña en canónicos.
**Opciones posibles:** ninguna — el ingrediente no existe en canónicos

**Tu decisión:** a, Pineapple Syrup

### 15. Almondine
**Problema:** Producto de almendra Bindi (posiblemente pasta de almendra o tarta). Sin match claro.
**Opciones posibles en canónicos:** `Croissant Almond`

**Tu decisión:** b, unmatched

### 16. Avocado Cheesecake
**Problema:** Cheesecake de aguacate — no existe en canónicos.
**Opciones posibles:** ninguna — el ingrediente no existe en canónicos

**Tu decisión:** _________

### 17. Chocolate Mousse Glass
**Problema:** Mousse de chocolate en vaso individual. No existe en canónicos.
**Opciones posibles en canónicos:** `Chocolate Gelato`

**Tu decisión:** c, Avocado Cheesecake

### 18. Chocolate Truffle Cake
**Problema:** Torta de trufa de chocolate. No existe en canónicos.
**Opciones posibles en canónicos:** `Cake Chocolate Fondant`, `Cake Chocolate Temptation`

**Tu decisión:** c, Chocolate Truffle Cake

### 19. Cornetto Blueberry & Chocolate Chips Vegan
**Problema:** Croissant vegano de arándano y chispas de choc. No existe en canónicos.
**Opciones posibles en canónicos:** `Croissant Chocolate`

**Tu decisión:**  c, Croissant Blueberry & Chocolate Chips Vegan

### 20. Decadent Cinnamon Brioche
**Problema:** Brioche de canela ≠ Cinnamon Roll. No existe brioche en canónicos.
**Opciones posibles en canónicos:** `Cinnamon Roll`

**Tu decisión:** a, Cinnamon Roll

### 21. Raspberry Passion Fruit Cake
**Problema:** Torta frambuesa-maracuyá. No existe en canónicos.
**Opciones posibles:** ninguna — el ingrediente no existe en canónicos

**Tu decisión:** c, Raspberry Passion Fruit Cake

### 22. Sicilian Cannoli
**Problema:** Cannoli siciliano — no existe en canónicos. Mapeo actual 'A Siciliana Mandarino' es claramente incorrecto (es una bebida).
**Opciones posibles:** ninguna — el ingrediente no existe en canónicos

**Tu decisión:** c, Sicilian Cannoli

### 23. Pistachio Profiteroles
**Problema:** Profiteroles de pistacho — no existe en canónicos. Mapeo actual 'Tapioca Pearls' incorrecto.
**Opciones posibles:** ninguna — el ingrediente no existe en canónicos

**Tu decisión:** c, Pistachio Profiteroles

### 24. Traditional New York Cheesecake
**Problema:** Cheesecake NY clásico. No existe como tal en canónicos.
**Opciones posibles en canónicos:** `Cheeseacake Strawberry`, `Cheesecake Brownie`, `Cheesecake Chocolate Ganache`, `Cheesecake Dulce de Leche`, `Cheesecake Pistachio`

**Tu decisión:** c, Traditional Cheesecake

### 25. Truffle Royale Cake
**Problema:** Torta Royale de trufa. No existe en canónicos. Mapeo actual 'Waffles Birthday Cake' incorrecto.
**Opciones posibles en canónicos:** `Cake Chocolate Fondant`, `Cake Chocolate Temptation`

**Tu decisión:** c, Truffle Royale Cake

---

## 3 NOT_A_RECIPE_INGREDIENT (propuestos — confirmar)

Propongo marcarlos como `unmatched`. ¿Confirmas?

- **Napkins** — Servilletas — suministro operacional → `unmatched`? [ X] sí  [ ] no
- **Clearly Cold Cleaner** — Limpiador industrial — no es ingrediente → `unmatched`? [ X] sí  [ ] no
- **Gran Riserva Coffee** — Blend café en grano para máquina — no ingrediente de receta (el canónico es Espresso) → `unmatched`? [ X] sí  [ ] no
