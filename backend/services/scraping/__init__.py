import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

# ============================================
# CONFIGURACIÓN DE LOGGING
# ============================================

def setup_scraping_logger():
    """
    Configura logger para el sistema de scraping.
    
    Features:
    - Log file con rotación automática
    - Console output para debugging
    - Formato detallado con timestamps
    - Niveles separados por severidad
    """
    
    # Crear directorio de logs si no existe
    log_dir = Path('logs')
    log_dir.mkdir(exist_ok=True)
    
    # Logger principal
    logger = logging.getLogger('scraper')
    logger.setLevel(logging.DEBUG)
    
    # Evitar duplicados
    if logger.handlers:
        return logger
    
    # ========== FILE HANDLER (todas las operaciones) ==========
    file_handler = RotatingFileHandler(
        'logs/scraping.log',
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    
    file_formatter = logging.Formatter(
        '%(asctime)s | %(name)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    
    # ========== ERROR FILE HANDLER (solo errores) ==========
    error_handler = RotatingFileHandler(
        'logs/scraping_errors.log',
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(file_formatter)
    
    # ========== CONSOLE HANDLER (development) ==========
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    
    console_formatter = logging.Formatter(
        '%(levelname)-8s | %(name)s | %(message)s'
    )
    console_handler.setFormatter(console_formatter)
    
    # Agregar handlers
    logger.addHandler(file_handler)
    logger.addHandler(error_handler)
    logger.addHandler(console_handler)
    
    # Log de inicio
    logger.info("=" * 60)
    logger.info("Scraping system logger initialized")
    logger.info("=" * 60)
    
    return logger

# Inicializar logger al importar módulo
setup_scraping_logger()

# ============================================
# EXPORTS
# ============================================

from .scraper_manager import ScraperManager
from .core.scraper_factory import ScraperFactory
from .utils.config_loader import ConfigLoader

__all__ = [
    'ScraperManager',
    'ScraperFactory',
    'ConfigLoader'
]

__version__ = '1.0.0'