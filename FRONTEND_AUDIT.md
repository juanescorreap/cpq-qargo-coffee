# Auditoría Front-End — CPQ Qargo Coffee

> Auditoría crítica (nivel Staff/Principal Front-End) del front-end de la app de
> costeo/pricing. Analizada sobre el código real del repo. Fecha: 2026-06-05.

## Reality check del stack

**No es React/Next/Tailwind-build.** Es una **MPA server-rendered**:

| Capa | Qué hay |
|---|---|
| Render | Jinja2, 46 templates por feature (`costs/`, `stores/`, `scraping/`…) + parciales `_*.html` |
| Interactividad | HTMX 2.0.4 (swaps de fragmentos) + Alpine 3.14.9 (dropdowns, menú móvil) |
| Estilos | **Tailwind por CDN** (`cdn.tailwindcss.com`) + `static/css/style.css` (354 líneas) |
| JS propio | `static/js/app.js` = **2 líneas de comentario** (vacío) |
| Backend UI | routers `*_ui.py` (HTML) separados de `*.py` (JSON API) |

Aciertos de entrada: separación API/UI limpia, JS propio ~0, Alpine solo para UI
local, carpetas por feature. Problemas graves concentrados en: **rendering HTMX**,
**Tailwind CDN**, **manejo de errores**.

---

## 1. Arquitectura y acoplamiento

**Bien:**
- Carpetas **por feature** (templates y routers) — escalable para equipo. Parciales con prefijo `_`.
- Separación **smart/dumb**: router `*_ui.py` (smart: query + render) ↔ template (dumb). Ej. `backend/routers/costs_ui.py:27-48`.
- Alpine encapsulado por componente (`x-data` local en `base.html:62`), sin estado global innecesario.

**Mal:**
- 🔴 **Routers UI consultan la BD directamente** (`costs_ui.py:32 db.query(Product)...`). La lógica "productos activos ordenados" se **duplica** entre `costs.py` (API) y `costs_ui.py` (UI). Acoplamiento UI↔ORM. Centralizar en un servicio/repository (igual que `CostCalculator` ya centraliza el costeo).
- 🟠 **Sin capa "fragmento vs página"**: cada página `extends base.html` y se sirve igual a navegación normal y a HTMX (ver §2.1).

---

## 2. Rendimiento y Core Web Vitals

### 2.1 🔴🔴 CRÍTICO — Páginas completas inyectadas en `#main-content`
Cada link del navbar: `hx-get="/costs/calculator" hx-target="#main-content"` (`base.html:85-89`).
Ese endpoint devuelve `calculator.html` → `{% extends "base.html" %}` (`calculator.html:1`).
**Ningún `*_ui.py` ramifica por header `HX-Request`** (grep vacío).

Consecuencia: HTMX inyecta un **documento completo** (`<head>`, navbar, footer)
dentro de `#main-content` → **navbar/footer duplicados anidados** en cada
navegación HTMX. Por URL directa (full load) funciona, por eso pasó
desapercibido; la navegación SPA-like está rota y duplica DOM.

### 2.2 🔴 Tailwind por CDN en producción
`base.html:15 <script src="https://cdn.tailwindcss.com">`. El propio CDN advierte
"should not be used in production": envía el **motor JIT completo al navegador**,
compila CSS en runtime (MutationObserver), causa **FOUC**, no purga, no cachea CSS
compilado, y es **render-blocking síncrono en `<head>`**. Mayor lastre de LCP/FCP.

### 2.3 🟠 Cadena render-blocking + sin SRI
3 scripts CDN + Google Fonts en `<head>`: Tailwind (sync), HTMX (sync, **con
`integrity`**), Alpine (`defer`, **sin SRI**, `base.html:43`). Dependencia de 3
orígenes externos = riesgo supply-chain + offline. Sin self-host ni bundle.

### 2.4 Bien
- `font-display: swap` + `preconnect` (`base.html:46-49`).
- `[x-cloak]` inline evita flash de Alpine (`base.html:12`).
- **Sin polling agresivo**: triggers por evento (`scraping/dashboard.html:66 load, scrapers-refresh from:body`), no `every Ns`.
- **Sin waterfalls de cliente**: datos precargados server-side; `_sizes` on-demand al cambiar producto (`calculator.html:41 hx-trigger=change`).
- `app.js` vacío → **0 KB de bundle JS propio**.

---

## 3. Estado y flujo de datos

- **Server state como fuente de verdad** vía HTMX — encaje correcto. No hay (ni hacen falta) Redux/Zustand.
- **Sin prop drilling** (no hay árbol de componentes cliente). Alpine sostiene estado efímero (`open`, `scOpen`).
- 🟡 **Sin caché de servidor en cliente**: cada navegación HTMX re-pega al server. `hx-boost`/`hx-history`/caché de GET mejoraría navegación repetida. No se necesita TanStack/SWR (eso es para SPAs).
- 🟡 Eventos custom Alpine↔HTMX (`from:body`) funcionan pero sin contrato documentado.

---

## 4. Robustez, A11y, tipado

### Robustez
- 🔴 **Sin manejo global de errores HTMX** (`htmx:responseError` no existe). HTMX solo swapea 2xx; un **500 → no pasa nada** (fallo silencioso). Sin toast/boundary global.
  - Excepción buena: `costs_ui.py:80-97` captura `ValueError/RecursionError` y renderiza `_result.html` con `error`. Pero es manual, no generalizado.
- 🟡 Loading: existe `#page-spinner` (`base.html:213`). Sin **skeletons** por sección.

### A11y
- Bien: `lang`, `<header><nav><main><footer>`, `aria-label`/`aria-expanded` en hamburguesa (`base.html:146-147`), `aria-hidden` en SVGs.
- 🟡 `lang="en"` pero contenido **mezcla español** ("Selecciona un producto" `costs_ui.py:82`).
- 🟡 Dropdown "Supply Chain" es `<button>`+`x-show` sin `aria-haspopup`/`aria-expanded` ni teclado (flechas/Escape).

### Tipado
- Routers Python **tipados** (`Optional[int]`, `Session`). Sin TS → la pregunta de `any` no aplica.
- 🟡 Templates Jinja sin contrato: el router pasa dicts libres (`breakdown`); si cambia la forma, rompe en runtime. Mitigable con dataclasses/Pydantic en el contexto.

---

## 5. Plan de acción priorizado (matriz)

| # | Iniciativa | Impacto | Esfuerzo | Blast radius | Adopción |
|---|---|---|---|---|---|
| 1 | Render fragmento vs página por `HX-Request` | 🔴 Alto | 🟢 Bajo | Todos los `*_ui.py` + `base.html` (con fachada = no rompe URLs) | **Fachada** (helper compat) |
| 2 | Tailwind CDN → build estático purgado (self-host) | 🔴 Alto | 🟡 Medio | Global (todos los templates) — riesgo de purgar clases dinámicas | Progresivo (CDN→build con safelist) |
| 3 | Handler global `htmx:responseError` + toast | 🟠 Medio | 🟢 Bajo | Global (1 archivo `app.js`) | Directo aditivo |
| 4 | Self-host + SRI de HTMX/Alpine | 🟠 Medio | 🟢 Bajo | `base.html` solo | Directo |
| 5 | Repository compartido para queries UI/API | 🟡 Medio | 🟡 Medio | routers `*` y `*_ui` | Fachada (servicio nuevo, migrar router por router) |
| 6 | Contratos tipados (dataclass) al contexto de template | 🟡 Bajo | 🟡 Medio | router por router | Progresivo |
| 7 | A11y dropdown (teclado/ARIA) + `lang` coherente | 🟡 Bajo | 🟢 Bajo | `base.html` + i18n | Directo |

### Iniciativa #1 — fragmento vs página (la clave)

**Fachada de compat**: layout que elige el padre según `HX-Request`, sin tocar URLs
ni el render por carga directa.

**Antes** (`costs_ui.py`):
```python
return templates.TemplateResponse("costs/calculator.html", {
    "request": request, "products": products, "stores": stores,
})
```
```jinja
{# calculator.html #}
{% extends "base.html" %}
{% block content %} ... {% endblock %}
```

**Después** — layout que conmuta:
```jinja
{# _layout.html  (nuevo) — base completa SOLO si no es HTMX #}
{% extends "base.html" if not request.headers.get("HX-Request") else "_bare.html" %}
```
```jinja
{# _bare.html (nuevo) — sin <head>/navbar/footer: solo el bloque content #}
{% block content %}{% endblock %}
```
```jinja
{# calculator.html — cambia 1 línea #}
{% extends "_layout.html" %}
{% block content %} ... {% endblock %}
```
Carga directa → página completa; navegación HTMX → **solo el contenido** en
`#main-content`. Cero cambios en routers, cero URLs rotas. Migración
template-por-template (`extends "base.html"` → `extends "_layout.html"`).

### Iniciativa #3 — errores HTMX globales (barato, alto valor)

**Antes:** nada → 500 = pantalla muda.

**Después** (`static/js/app.js`, hoy vacío):
```javascript
// Global HTMX error surface — no swap happens on non-2xx, so notify here.
document.body.addEventListener("htmx:responseError", (e) => {
  const code = e.detail.xhr.status;
  showToast(`Error ${code}: la operación falló. Reintenta.`);
});
document.body.addEventListener("htmx:sendError", () =>
  showToast("Sin conexión con el servidor."));

function showToast(msg) {
  const t = document.createElement("div");
  t.role = "alert";
  t.className = "fixed bottom-4 right-4 bg-red-600 text-white px-4 py-2 rounded-lg shadow-lg z-[100]";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 5000);
}
```

### Iniciativa #2 — Tailwind CDN → build

Sin Node en el repo (es Python), usar el binario **Tailwind standalone CLI** (no
requiere `package.json`):
```
tailwindcss -i input.css -o static/css/tailwind.css --content "backend/templates/**/*.html" --minify
```
`base.html`: cambiar el `<script src="cdn.tailwindcss.com">` por
`<link rel="stylesheet" href="{{ url_for('static', path='css/tailwind.css') }}">`.
El `tailwind.config` inline (`base.html:17-34`) pasa a `tailwind.config.js`.
Riesgo (blast radius): clases generadas dinámicamente en Python (ej. `_result.html`
por estado) pueden purgarse → usar `safelist`. Adopción progresiva: generar el
build y comparar visualmente antes de quitar el CDN.

---

## Veredicto

Base **sólida y bien separada** para una MPA server-rendered; JS propio ~0 y
ausencia de waterfalls de cliente son aciertos reales. Pero **no está listo para
"ultra rápido" en prod** por dos cosas concretas y baratas: el **render de página
completa en swaps HTMX** (#1, bug visible) y **Tailwind CDN** (#2, lastre CWV).
Con #1+#2+#3 (todas bajo esfuerzo) salta de "renderiza" a "rápido y robusto".

**Quick wins recomendados primero:** #1 (fachada `_layout.html`) + #3 (errores HTMX)
— mejor ratio impacto/esfuerzo y bajo riesgo.
