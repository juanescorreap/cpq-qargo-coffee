"""
Pydantic v2 schemas for scraping API endpoints.

Organización:
  Enums       — ScraperBusinessType, ScraperType
  Requests    — ScrapeIngredientRequest, ScrapeIngredientsBatchRequest,
                ScrapeCompetitorMenuRequest, TestScraperRequest
  Responses   — ScraperInfoResponse, ScrapedProductResponse,
                ScrapeIngredientResponse, ScrapeIngredientsBatchResponse,
                ScrapeCompetitorMenuResponse, TestScraperResponse,
                ScraperStatusResponse
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ============================================
# ENUMS
# ============================================

class ScraperBusinessType(str, Enum):
    """Tipo de negocio del scraper."""
    COMPETITOR = "competitor"
    SUPPLIER = "supplier"


class ScraperType(str, Enum):
    """Categoría técnica del sitio scrapeado."""
    RESTAURANT = "restaurant"
    RETAIL = "retail"
    MARKETPLACE = "marketplace"
    CUSTOM = "custom"


# ============================================
# REQUEST SCHEMAS
# ============================================

class ScrapeIngredientRequest(BaseModel):
    """
    Solicita el scraping del precio actual de un ingrediente.

    Si ``update_db`` es True y el precio cambió, el sistema actualiza
    ``ingredient.purchase_price`` y agrega una fila en
    ``ingredient_price_history``.
    """

    ingredient_id: int = Field(
        ...,
        gt=0,
        description="ID del ingrediente en la base de datos.",
    )
    update_db: bool = Field(
        True,
        description="Si True, persiste el nuevo precio en DB cuando cambia.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "ingredient_id": 1,
                "update_db": True,
            }
        }
    )


class ScrapeIngredientsBatchRequest(BaseModel):
    """
    Solicita el scraping simultáneo de múltiples ingredientes.

    Máximo 100 IDs por request para evitar timeouts en el endpoint.
    El sistema procesa los ingredientes secuencialmente (no en paralelo).
    """

    ingredient_ids: List[int] = Field(
        ...,
        description="IDs de los ingredientes a scrape. Entre 1 y 100.",
    )
    update_db: bool = Field(
        True,
        description="Si True, persiste los precios actualizados en DB.",
    )
    supplier_only: bool = Field(
        False,
        description=(
            "Si True, omite ingredientes cuyo scraper es de tipo 'competitor'. "
            "Útil para actualizar solo costos de insumos sin tocar análisis competitivo."
        ),
    )

    @field_validator("ingredient_ids")
    @classmethod
    def validate_ids(cls, v: List[int]) -> List[int]:
        if not v:
            raise ValueError("ingredient_ids no puede estar vacío")
        if len(v) > 100:
            raise ValueError("Máximo 100 ingredientes por batch")
        if any(i <= 0 for i in v):
            raise ValueError("Todos los IDs deben ser > 0")
        return v

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "ingredient_ids": [1, 2, 3, 4, 5],
                "update_db": True,
                "supplier_only": False,
            }
        }
    )


class ScrapeCompetitorMenuRequest(BaseModel):
    """
    Solicita el scraping del menú de un competidor.

    El sistema busca los productos usando cada término en ``search_queries``
    y persiste los resultados en ``competitor_products``.  Si ``search_queries``
    es None, se usan los términos por defecto del sistema (café, latte, etc.).
    """

    competitor_id: int = Field(
        ...,
        gt=0,
        description="ID del competidor en la base de datos.",
    )
    search_queries: Optional[List[str]] = Field(
        None,
        description=(
            "Términos de búsqueda. Si None, usa las búsquedas por defecto "
            "del sistema (cappuccino, latte, americano, …)."
        ),
    )
    limit_per_query: int = Field(
        10,
        ge=1,
        le=50,
        description="Máximo de productos a extraer por término de búsqueda.",
    )

    @field_validator("search_queries")
    @classmethod
    def validate_queries(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            if not v:
                raise ValueError("search_queries no puede ser una lista vacía; usa None para defaults")
            if any(not q.strip() for q in v):
                raise ValueError("Ningún término de búsqueda puede estar vacío")
            if len(v) > 20:
                raise ValueError("Máximo 20 términos de búsqueda por request")
        return v

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "competitor_id": 1,
                "search_queries": ["cappuccino", "latte", "americano"],
                "limit_per_query": 10,
            }
        }
    )


class TestScraperRequest(BaseModel):
    """
    Ejecuta un scraper en modo prueba sin escribir en la base de datos.

    Útil para validar que los selectores CSS/XPath de un scraper recién
    configurado funcionan correctamente antes de activarlo en producción.
    Devuelve una muestra de productos sin persistirlos.
    """

    scraper_id: str = Field(
        ...,
        min_length=1,
        description="ID del scraper a probar (ej: 'competitor_001').",
    )
    search_query: str = Field(
        "coffee",
        min_length=1,
        description="Término de búsqueda de prueba.",
    )
    limit: int = Field(
        3,
        ge=1,
        le=10,
        description="Máximo de productos a devolver en el test.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "scraper_id": "competitor_001",
                "search_query": "coffee",
                "limit": 3,
            }
        }
    )


# ============================================
# RESPONSE SCHEMAS
# ============================================

class ScraperInfoResponse(BaseModel):
    """Metadatos de un scraper configurado en el sistema."""

    id: str = Field(description="Identificador único del scraper (filename YAML sin extensión).")
    name: str = Field(description="Nombre legible del negocio scrapeado.")
    type: ScraperBusinessType = Field(description="'competitor' o 'supplier'.")
    scraper_type: ScraperType = Field(description="Categoría técnica del sitio.")
    base_url: str = Field(description="URL raíz del sitio scrapeado.")
    enabled: bool = Field(description="Si el scraper está activo.")
    priority: Optional[int] = Field(None, description="Orden de ejecución en batch (menor = primero).")
    schedule: Optional[str] = Field(None, description="Frecuencia sugerida: 'daily', 'weekly', 'monthly'.")
    notes: Optional[str] = Field(None, description="Notas del operador sobre este scraper.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "competitor_001",
                "name": "Competidor Cafetería A",
                "type": "competitor",
                "scraper_type": "restaurant",
                "base_url": "https://ejemplo-competidor.com",
                "enabled": True,
                "priority": 1,
                "schedule": "weekly",
                "notes": "Actualizar selectores si el sitio cambia layout",
            }
        }
    )


class ScrapedProductResponse(BaseModel):
    """Un producto individual extraído durante un scraping."""

    product_name: str = Field(description="Nombre del producto tal como aparece en el sitio.")
    price: Decimal = Field(description="Precio en la moneda indicada.")
    currency: str = Field(default="COP", description="Código ISO 4217 de la moneda.")
    unit: Optional[str] = Field(None, description="Unidad / tamaño (ej: '500g', '12oz').")
    category: Optional[str] = Field(None, description="Categoría reportada por el sitio.")
    url: Optional[str] = Field(None, description="URL de la página del producto.")
    image_url: Optional[str] = Field(None, description="URL de la imagen del producto.")
    availability: bool = Field(default=True, description="False si el sitio indica sin stock.")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Campos extra específicos del scraper (rating, reviews, etc.).",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "product_name": "Cappuccino Grande",
                "price": "8500.00",
                "currency": "COP",
                "unit": "16oz",
                "category": "Bebidas Calientes",
                "url": "https://ejemplo-competidor.com/menu/cappuccino",
                "image_url": "https://cdn.ejemplo.com/img/cappuccino.jpg",
                "availability": True,
                "metadata": {"rating": "4.5", "reviews_count": "128"},
            }
        }
    )


class ScrapeIngredientResponse(BaseModel):
    """
    Resultado del scraping de un ingrediente individual.

    ``success=False`` no levanta un HTTP error; el error está en el campo
    ``error`` para que un batch pueda reportar resultados mixtos.
    """

    success: bool
    ingredient_id: Optional[int] = None
    ingredient_name: Optional[str] = None
    old_price: Optional[Decimal] = Field(None, description="Precio antes del scraping.")
    new_price: Optional[Decimal] = Field(None, description="Precio encontrado en el sitio.")
    price_change: Optional[Decimal] = Field(None, description="new_price − old_price.")
    price_change_pct: Optional[float] = Field(
        None, description="Cambio porcentual respecto al precio anterior."
    )
    scraper_id: Optional[str] = None
    business_name: Optional[str] = None
    scraped_at: Optional[datetime] = None
    updated_db: bool = Field(default=False, description="True si el precio fue persistido en DB.")
    error: Optional[str] = Field(None, description="Mensaje de error si success=False.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "ingredient_id": 1,
                "ingredient_name": "Leche entera Alpina 1L",
                "old_price": "4500.00",
                "new_price": "4800.00",
                "price_change": "300.00",
                "price_change_pct": 6.67,
                "scraper_id": "supplier_001",
                "business_name": "Proveedor Retail A",
                "scraped_at": "2025-05-20T10:30:00",
                "updated_db": True,
                "error": None,
            }
        }
    )


class ScrapeIngredientsBatchResponse(BaseModel):
    """Resultado agregado de un batch de scraping de ingredientes."""

    total: int = Field(description="Total de ingredientes procesados.")
    success: int = Field(description="Scrapers exitosos.")
    failed: int = Field(description="Scrapers fallidos.")
    skipped: int = Field(description="Ingredientes omitidos (ej: filtro supplier_only).")
    results: List[ScrapeIngredientResponse] = Field(
        description="Resultado individual por ingrediente."
    )
    execution_time_ms: float = Field(description="Tiempo total de ejecución en milisegundos.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total": 5,
                "success": 4,
                "failed": 1,
                "skipped": 0,
                "results": [],
                "execution_time_ms": 22345.6,
            }
        }
    )


class ScrapeCompetitorMenuResponse(BaseModel):
    """Resultado del scraping del menú de un competidor."""

    success: bool
    competitor_id: Optional[int] = None
    competitor_name: Optional[str] = None
    scraper_id: Optional[str] = None
    business_name: Optional[str] = None
    total_products_found: int = Field(
        default=0,
        description="Productos extraídos del sitio en esta ejecución.",
    )
    new_products: int = Field(
        default=0,
        description="Productos insertados por primera vez en competitor_products.",
    )
    updated_products: int = Field(
        default=0,
        description="Productos cuyo precio fue actualizado.",
    )
    errors: List[str] = Field(
        default_factory=list,
        description="Errores de queries o de DB (no fatales).",
    )
    execution_time_ms: float = Field(default=0.0)
    error: Optional[str] = Field(None, description="Error fatal si success=False.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "competitor_id": 1,
                "competitor_name": "Competidor Cafetería A",
                "scraper_id": "competitor_001",
                "business_name": "Competidor Cafetería A",
                "total_products_found": 25,
                "new_products": 3,
                "updated_products": 22,
                "errors": [],
                "execution_time_ms": 15234.5,
                "error": None,
            }
        }
    )


class TestScraperResponse(BaseModel):
    """
    Resultado de un test de scraper sin escritura en DB.

    Incluye la lista completa de productos extraídos (hasta ``limit``)
    para que el operador verifique que los datos son correctos.
    """

    success: bool
    scraper_id: str
    business_name: Optional[str] = None
    products_found: int = Field(default=0)
    products: List[ScrapedProductResponse] = Field(default_factory=list)
    execution_time_ms: float = Field(default=0.0)
    error: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "scraper_id": "competitor_001",
                "business_name": "Competidor Cafetería A",
                "products_found": 3,
                "products": [
                    {
                        "product_name": "Cappuccino Grande",
                        "price": "8500.00",
                        "currency": "COP",
                        "unit": "16oz",
                        "category": "Bebidas Calientes",
                        "url": None,
                        "image_url": None,
                        "availability": True,
                        "metadata": {},
                    }
                ],
                "execution_time_ms": 3456.7,
                "error": None,
            }
        }
    )


class ScraperStatusResponse(BaseModel):
    """
    Estado de ejecución histórico de un scraper.

    Este schema es para un endpoint de monitoreo futuro; los datos
    provienen de logs / tablas de auditoría, no del scraper en vivo.
    """

    scraper_id: str
    enabled: bool
    last_execution: Optional[datetime] = Field(
        None, description="Timestamp de la última ejecución completada."
    )
    total_executions: int = Field(default=0, description="Total de ejecuciones registradas.")
    success_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Porcentaje de ejecuciones exitosas (0–100).",
    )
    average_execution_time_ms: Optional[float] = Field(
        None, description="Promedio de duración de ejecuciones exitosas."
    )
    last_error: Optional[str] = Field(
        None, description="Mensaje del último error registrado, o None si no hay."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "scraper_id": "competitor_001",
                "enabled": True,
                "last_execution": "2025-05-20T09:15:00",
                "total_executions": 145,
                "success_rate": 94.5,
                "average_execution_time_ms": 8234.6,
                "last_error": None,
            }
        }
    )
