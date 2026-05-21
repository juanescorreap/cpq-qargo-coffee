# scripts/health_check_scraping.sh

#!/bin/bash

# ============================================
# HEALTH CHECK - Sistema de Scraping
# ============================================
# Verifica que el sistema de scraping funciona correctamente.
#
# Uso:
#   bash scripts/health_check_scraping.sh

echo "🏥 Ejecutando Health Check del Sistema de Scraping..."
echo ""

ERRORS=0

# ============================================
# 1. VERIFICAR SERVIDOR
# ============================================
echo "🌐 Verificando servidor..."

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/scraping/available-scrapers)

if [ "$HTTP_CODE" == "200" ]; then
    echo "✅ Servidor respondiendo (HTTP $HTTP_CODE)"
else
    echo "❌ Servidor no responde correctamente (HTTP $HTTP_CODE)"
    ERRORS=$((ERRORS + 1))
fi

echo ""

# ============================================
# 2. VERIFICAR SCRAPERS DISPONIBLES
# ============================================
echo "🤖 Verificando scrapers disponibles..."

SCRAPERS=$(curl -s http://localhost:8000/api/scraping/available-scrapers)
SCRAPER_COUNT=$(echo "$SCRAPERS" | python3 -c "import sys, json; print(len(json.load(sys.stdin)))")

if [ "$SCRAPER_COUNT" -gt 0 ]; then
    echo "✅ $SCRAPER_COUNT scrapers configurados"
else
    echo "❌ No se encontraron scrapers configurados"
    ERRORS=$((ERRORS + 1))
fi

echo ""

# ============================================
# 3. VERIFICAR LOGS
# ============================================
echo "📝 Verificando logs..."

if [ -f "logs/scraping.log" ]; then
    LOG_SIZE=$(wc -l < logs/scraping.log)
    echo "✅ Log file existe ($LOG_SIZE líneas)"
    
    # Verificar errores recientes (últimas 100 líneas)
    ERROR_COUNT=$(tail -n 100 logs/scraping.log | grep -c "ERROR" || true)
    
    if [ "$ERROR_COUNT" -gt 10 ]; then
        echo "⚠️  Advertencia: $ERROR_COUNT errores en últimas 100 líneas"
    else
        echo "✅ Nivel de errores normal ($ERROR_COUNT en últimas 100 líneas)"
    fi
else
    echo "⚠️  Log file no encontrado"
fi

echo ""

# ============================================
# 4. VERIFICAR BASE DE DATOS
# ============================================
echo "🗄️  Verificando base de datos..."

python3 << 'PYEOF'
try:
    from backend.database import SessionLocal
    from backend.models import Ingredient, Competitor
    
    db = SessionLocal()
    
    # Contar ingredientes con source_url
    ingredients_with_url = db.query(Ingredient).filter(
        Ingredient.source_url.isnot(None)
    ).count()
    
    # Contar competidores activos
    active_competitors = db.query(Competitor).filter(
        Competitor.is_active == True
    ).count()
    
    print(f"✅ DB conectada:")
    print(f"   - Ingredientes con URL: {ingredients_with_url}")
    print(f"   - Competidores activos: {active_competitors}")
    
    db.close()
except Exception as e:
    print(f"❌ Error en DB: {e}")
    exit(1)
PYEOF

if [ $? -ne 0 ]; then
    ERRORS=$((ERRORS + 1))
fi

echo ""

# ============================================
# 5. TEST DE SCRAPER (si hay scrapers)
# ============================================
if [ "$SCRAPER_COUNT" -gt 0 ]; then
    echo "🧪 Ejecutando test de scraper..."
    
    # Obtener primer scraper ID
    FIRST_SCRAPER=$(echo "$SCRAPERS" | python3 -c "import sys, json; data=json.load(sys.stdin); print(data[0]['id'] if data else '')")
    
    if [ -n "$FIRST_SCRAPER" ]; then
        echo "   Probando scraper: $FIRST_SCRAPER"
        
        TEST_RESULT=$(curl -s -X POST http://localhost:8000/api/scraping/test \
            -H "Content-Type: application/json" \
            -d "{\"scraper_id\": \"$FIRST_SCRAPER\", \"search_query\": \"test\", \"limit\": 2}")
        
        TEST_SUCCESS=$(echo "$TEST_RESULT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('success', False))")
        
        if [ "$TEST_SUCCESS" == "True" ]; then
            echo "✅ Test de scraper exitoso"
        else
            echo "❌ Test de scraper falló"
            ERRORS=$((ERRORS + 1))
        fi
    fi
fi

echo ""

# ============================================
# 6. VERIFICAR PLAYWRIGHT
# ============================================
echo "🌐 Verificando Playwright..."

python3 << 'PYEOF'
try:
    from playwright.sync_api import sync_playwright
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto('https://www.google.com')
        title = page.title()
        browser.close()
    
    print(f"✅ Playwright funciona correctamente")
except Exception as e:
    print(f"❌ Error en Playwright: {e}")
    exit(1)
PYEOF

if [ $? -ne 0 ]; then
    ERRORS=$((ERRORS + 1))
fi

echo ""

# ============================================
# RESUMEN
# ============================================
echo "╔════════════════════════════════════════════════╗"

if [ $ERRORS -eq 0 ]; then
    echo "║   ✅ HEALTH CHECK: TODO OK                    ║"
    echo "╚════════════════════════════════════════════════╝"
    exit 0
else
    echo "║   ❌ HEALTH CHECK: $ERRORS ERRORES ENCONTRADOS     ║"
    echo "╚════════════════════════════════════════════════╝"
    echo ""
    echo "Por favor revisa los errores arriba."
    exit 1
fi