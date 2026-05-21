#!/bin/bash

# ============================================
# SCRIPT DE INSTALACIÓN - SISTEMA DE SCRAPING
# ============================================
# Este script instala y configura el sistema de scraping.
#
# Uso:
#   bash scripts/install_scraping.sh

set -e  # Exit on error

echo "🚀 Instalando Sistema de Scraping..."
echo ""

# ============================================
# 0. DETECTAR INTÉRPRETE PYTHON (venv o sistema)
# ============================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
    PIP="$PROJECT_ROOT/.venv/bin/pip"
    PLAYWRIGHT_CMD="$PROJECT_ROOT/.venv/bin/playwright"
    echo "🐍 Usando virtualenv: $PROJECT_ROOT/.venv"
else
    PYTHON="python3"
    PIP="pip3"
    PLAYWRIGHT_CMD="playwright"
    echo "🐍 Usando Python del sistema"
fi
echo ""

# ============================================
# 1. VERIFICAR VERSIÓN DE PYTHON
# ============================================
echo "📌 Verificando versión de Python..."

PYTHON_VERSION=$("$PYTHON" --version 2>&1 | awk '{print $2}')
REQUIRED_VERSION="3.11"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo "❌ ERROR: Se requiere Python 3.11 o superior."
    echo "   Versión actual: $PYTHON_VERSION"
    exit 1
fi

echo "✅ Python $PYTHON_VERSION detectado"
echo ""

# ============================================
# 2. INSTALAR DEPENDENCIAS
# ============================================
echo "📦 Instalando dependencias de Python..."

# Verificar que requirements.txt existe
if [ ! -f "$PROJECT_ROOT/requirements.txt" ]; then
    echo "❌ ERROR: requirements.txt no encontrado"
    exit 1
fi

# Instalar dependencias principales
"$PIP" install -r "$PROJECT_ROOT/requirements.txt"

# Instalar dependencias de scraping
echo "📦 Instalando paquetes específicos de scraping..."
"$PIP" install "pyyaml>=6.0" "playwright>=1.40.0"

echo "✅ Dependencias instaladas"
echo ""

# ============================================
# 3. INSTALAR PLAYWRIGHT BROWSERS
# ============================================
echo "🌐 Instalando navegadores de Playwright..."

"$PLAYWRIGHT_CMD" install chromium

echo "✅ Chromium instalado"
echo ""

# ============================================
# 4. VERIFICAR ESTRUCTURA DE DIRECTORIOS
# ============================================
echo "📁 Verificando estructura de directorios..."

REQUIRED_DIRS=(
    "$PROJECT_ROOT/backend/services/scraping/config"
    "$PROJECT_ROOT/backend/services/scraping/config/competitors"
    "$PROJECT_ROOT/backend/services/scraping/config/suppliers"
    "$PROJECT_ROOT/backend/services/scraping/core"
    "$PROJECT_ROOT/backend/services/scraping/utils"
    "$PROJECT_ROOT/backend/services/scraping/tests"
    "$PROJECT_ROOT/logs"
)

for dir in "${REQUIRED_DIRS[@]}"; do
    if [ ! -d "$dir" ]; then
        echo "📁 Creando directorio: $dir"
        mkdir -p "$dir"
    fi
done

echo "✅ Estructura de directorios verificada"
echo ""

# ============================================
# 5. CREAR CONFIGS DE EJEMPLO (SI NO EXISTEN)
# ============================================
echo "📄 Verificando archivos de configuración..."

# Verificar registry
if [ ! -f "$PROJECT_ROOT/backend/services/scraping/config/scrapers_registry.yaml" ]; then
    echo "⚠️  scrapers_registry.yaml no encontrado."
    echo "   Por favor crea este archivo manualmente siguiendo la documentación."
    echo "   Template: backend/services/scraping/config/README.md"
fi

# Verificar al menos un scraper de ejemplo
COMPETITOR_COUNT=$(find "$PROJECT_ROOT/backend/services/scraping/config/competitors" -name "*.yaml" 2>/dev/null | wc -l)
SUPPLIER_COUNT=$(find "$PROJECT_ROOT/backend/services/scraping/config/suppliers" -name "*.yaml" 2>/dev/null | wc -l)

if [ "$COMPETITOR_COUNT" -eq 0 ] && [ "$SUPPLIER_COUNT" -eq 0 ]; then
    echo "⚠️  No se encontraron configuraciones de scrapers."
    echo "   Crea al menos un scraper siguiendo:"
    echo "   backend/services/scraping/config/README.md"
fi

echo "✅ Configuraciones verificadas"
echo ""

# ============================================
# 6. VERIFICAR CONECTIVIDAD DE RED
# ============================================
echo "🌐 Verificando conectividad de red..."

# Test de conectividad básico
if curl -s --head --connect-timeout 5 https://www.google.com > /dev/null; then
    echo "✅ Conectividad de red OK"
else
    echo "⚠️  Advertencia: Problemas de conectividad detectados"
    echo "   El scraping requiere acceso a Internet"
fi
echo ""

# ============================================
# 7. TEST DE INSTALACIÓN
# ============================================
echo "🧪 Ejecutando test de instalación..."

# Test de imports
"$PYTHON" << EOF
import sys, os
sys.path.insert(0, "$PROJECT_ROOT")
os.chdir("$PROJECT_ROOT")
try:
    from backend.services.scraping.scraper_manager import ScraperManager
    from backend.services.scraping.core.scraper_factory import ScraperFactory
    from backend.services.scraping.utils.config_loader import ConfigLoader
    print("✅ Imports exitosos")
except ImportError as e:
    print(f"❌ Error en imports: {e}")
    sys.exit(1)
EOF

if [ $? -ne 0 ]; then
    echo "❌ Test de instalación fallido"
    exit 1
fi

echo ""

# ============================================
# 8. CONFIGURAR LOGGING
# ============================================
echo "📝 Configurando logging..."

# Crear directorio de logs si no existe
mkdir -p "$PROJECT_ROOT/logs"

# Crear archivo de log vacío
touch "$PROJECT_ROOT/logs/scraping.log"

# Configurar rotación de logs (opcional - requiere logrotate)
if command -v logrotate &> /dev/null; then
    echo "📝 Configurando rotación de logs..."
    
    cat > /tmp/scraping-logrotate.conf << 'LOGROTATE'
/path/to/your/project/logs/scraping.log {
    daily
    rotate 30
    compress
    delaycompress
    notifempty
    create 644 root root
    sharedscripts
}
LOGROTATE
    
    echo "   Archivo de configuración creado en: /tmp/scraping-logrotate.conf"
    echo "   Muévelo a /etc/logrotate.d/ para activar rotación automática"
fi

echo "✅ Logging configurado"
echo ""

# ============================================
# 9. VERIFICAR BASE DE DATOS
# ============================================
echo "🗄️  Verificando conexión a base de datos..."

"$PYTHON" << EOF
import sys, os
sys.path.insert(0, "$PROJECT_ROOT")
os.chdir("$PROJECT_ROOT")
try:
    from sqlalchemy import text
    from backend.database import SessionLocal
    db = SessionLocal()
    db.execute(text('SELECT 1'))
    db.close()
    print("✅ Conexión a DB exitosa")
except Exception as e:
    print(f"❌ Error conectando a DB: {e}")
    sys.exit(1)
EOF

if [ $? -ne 0 ]; then
    echo "⚠️  No se pudo conectar a la base de datos"
    echo "   Verifica tu configuración en .env"
fi

echo ""

# ============================================
# 10. RESUMEN
# ============================================
echo "╔════════════════════════════════════════════════╗"
echo "║                                                ║"
echo "║   ✅ INSTALACIÓN COMPLETADA                   ║"
echo "║                                                ║"
echo "╚════════════════════════════════════════════════╝"
echo ""
echo "📋 Próximos pasos:"
echo ""
echo "1. Configurar scrapers:"
echo "   → Editar: backend/services/scraping/config/scrapers_registry.yaml"
echo "   → Crear configs en: config/competitors/ y config/suppliers/"
echo "   → Ver guía: backend/services/scraping/config/README.md"
echo ""
echo "2. Actualizar selectores CSS:"
echo "   → Inspeccionar sitios reales con Chrome DevTools"
echo "   → Copiar selectores a archivos YAML"
echo "   → Ver guía: docs/SCRAPING_MAINTENANCE.md"
echo ""
echo "3. Probar instalación:"
echo "   → python3 -m pytest backend/services/scraping/tests/"
echo "   → curl http://localhost:8000/api/scraping/available-scrapers"
echo ""
echo "4. Iniciar servidor:"
echo "   → python3 -m backend.main"
echo "   → Navegar a: http://localhost:8000/scraping"
echo ""
echo "📚 Documentación:"
echo "   → Técnica: docs/SCRAPING_SYSTEM.md"
echo "   → Mantenimiento: docs/SCRAPING_MAINTENANCE.md"
echo "   → API: http://localhost:8000/docs"
echo ""
echo "🎉 ¡Sistema listo para usar!"
echo ""