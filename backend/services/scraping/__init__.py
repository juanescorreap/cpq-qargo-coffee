import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

# ============================================
# LOGGING CONFIGURATION
# ============================================

def setup_scraping_logger():
    """
    Configure the logger for the scraping system.

    Features:
    - Log file with automatic rotation
    - Console output for debugging
    - Detailed format with timestamps
    - Levels separated by severity
    """

    # Create logs directory if it does not exist
    log_dir = Path('logs')
    log_dir.mkdir(exist_ok=True)

    # Main logger
    logger = logging.getLogger('scraper')
    logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    # ========== FILE HANDLER (all operations) ==========
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

    # ========== ERROR FILE HANDLER (errors only) ==========
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

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(error_handler)
    logger.addHandler(console_handler)

    # Startup log
    logger.info("=" * 60)
    logger.info("Scraping system logger initialized")
    logger.info("=" * 60)

    return logger

# Initialise logger on module import
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