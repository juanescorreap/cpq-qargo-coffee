"""
Scraping API endpoints.

All endpoints delegate to ScraperManager which orchestrates the scraping
pipeline (ConfigLoader → ScraperFactory → ConfigurableScraper → DB).

Design decisions
----------------
- Scraping operations are synchronous at this scale.  BackgroundTasks is
  used for the batch endpoint so the HTTP response returns immediately;
  the actual work runs after the response is sent in the same process.
  If the workload grows, swap BackgroundTasks for an ARQ/Celery queue.
- All endpoints return 200 (or 202 for batch).  Business failures
  (scraper not found, ingredient has no URL, etc.) are encoded in
  ``success=False`` + ``error=<message>`` rather than 4xx codes so that
  batch callers can collect mixed results without catching exceptions.
- 500 is only raised for truly unexpected exceptions that the manager
  did not handle.
"""

import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.database import SessionLocal, get_db
from backend.services.scraping.core.exceptions import ScraperNotFoundError
from backend.services.scraping.scraper_manager import ScraperManager
from backend.schemas.scraping import (
    ScraperInfoResponse,
    ScraperStatusResponse,
    ScrapedProductResponse,
    ScrapeCompetitorMenuRequest,
    ScrapeCompetitorMenuResponse,
    ScrapeIngredientRequest,
    ScrapeIngredientResponse,
    ScrapeIngredientsBatchRequest,
    ScrapeIngredientsBatchResponse,
    TestScraperRequest,
    TestScraperResponse,
)

router = APIRouter(prefix="/api", tags=["scraping"])
logger = logging.getLogger("api.scraping")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _manager(db: Session) -> ScraperManager:
    """Construct a ScraperManager for the current request."""
    return ScraperManager(db)


# ---------------------------------------------------------------------------
# Information endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/scraping/scrapers",
    response_model=List[ScraperInfoResponse],
    summary="Listar scrapers disponibles",
    description="""
Retorna todos los scrapers configurados en el sistema, ordenados por prioridad.

Cada entrada incluye:
- **id** / **name**: identificador y nombre legible del negocio scrapeado.
- **type**: `competitor` o `supplier`.
- **scraper_type**: `restaurant`, `retail`, `marketplace`, `custom`.
- **enabled**: si el scraper está activo.
- **priority** / **schedule**: orden y frecuencia de ejecución en batch.
    """,
)
def list_scrapers(db: Session = Depends(get_db)) -> List[ScraperInfoResponse]:
    try:
        return _manager(db).list_available_scrapers()
    except Exception as exc:
        logger.error("Error listing scrapers: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error listing scrapers: {exc}",
        )


@router.get(
    "/scraping/scrapers/{scraper_id}/status",
    response_model=ScraperStatusResponse,
    summary="Status de un scraper",
    description="""
Devuelve información de estado de un scraper.

> **Nota:** Las estadísticas de ejecución (`total_executions`, `success_rate`,
> etc.) son un placeholder hasta que se implemente una tabla de auditoría de
> ejecuciones. Por ahora se devuelven en cero.
    """,
)
def get_scraper_status(
    scraper_id: str,
    db: Session = Depends(get_db),
) -> ScraperStatusResponse:
    try:
        _manager(db).get_scraper(scraper_id)  # raises ScraperNotFoundError if absent
    except ScraperNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scraper '{scraper_id}' not found",
        )
    except Exception as exc:
        logger.error("Error fetching status for '%s': %s", scraper_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )

    # TODO: query an execution_log table once it exists.
    return ScraperStatusResponse(
        scraper_id=scraper_id,
        enabled=True,
        last_execution=None,
        total_executions=0,
        success_rate=0.0,
        average_execution_time_ms=None,
        last_error=None,
    )


# ---------------------------------------------------------------------------
# Scraping endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/scraping/ingredient",
    response_model=ScrapeIngredientResponse,
    status_code=status.HTTP_200_OK,
    summary="Scrape precio de un ingrediente",
    description="""
Navega al `source_url` del ingrediente, extrae el precio y, opcionalmente,
lo persiste en la base de datos.

**Requisitos:**
- El ingrediente debe existir en DB.
- Debe tener `source_url` definido.
- Debe existir un scraper configurado para ese dominio.

**Comportamiento ante fallos:**
- Devuelve `200` con `success=false` y `error` descriptivo (no lanza 4xx/5xx).
- Sólo lanza `500` para errores completamente inesperados.
    """,
)
def scrape_ingredient(
    request: ScrapeIngredientRequest,
    db: Session = Depends(get_db),
) -> ScrapeIngredientResponse:
    try:
        result = _manager(db).scrape_ingredient(
            ingredient_id=request.ingredient_id,
            update_db=request.update_db,
        )
        return result
    except Exception as exc:
        logger.error("Unexpected error scraping ingredient %d: %s", request.ingredient_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@router.post(
    "/scraping/ingredients/batch",
    response_model=ScrapeIngredientsBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Scrape múltiples ingredientes (batch)",
    description="""
Lanza el scraping de múltiples ingredientes en una tarea de background.

- La respuesta `202 Accepted` se devuelve **inmediatamente** con contadores en cero.
- El scraping real ocurre después de que la respuesta fue enviada.
- Máximo **100** ingredientes por request.

> Para monitorear el progreso, consulta los logs o implementa un endpoint
> de polling con una tabla de auditoría (roadmap).
    """,
)
def scrape_ingredients_batch(
    request: ScrapeIngredientsBatchRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> ScrapeIngredientsBatchResponse:

    def _run(ingredient_ids: list, update_db: bool, supplier_only: bool) -> None:
        """Background task — uses its own DB session."""
        bg_db = SessionLocal()
        try:
            mgr = ScraperManager(bg_db)
            success = failed = 0
            for ing_id in ingredient_ids:
                r = mgr.scrape_ingredient(ing_id, update_db=update_db)
                if r["success"]:
                    success += 1
                else:
                    failed += 1
            logger.info(
                "Batch scraping complete: %d success, %d failed / %d total",
                success, failed, len(ingredient_ids),
            )
        except Exception as exc:
            logger.error("Batch scraping task failed: %s", exc)
        finally:
            bg_db.close()

    background_tasks.add_task(
        _run,
        ingredient_ids=request.ingredient_ids,
        update_db=request.update_db,
        supplier_only=request.supplier_only,
    )

    return ScrapeIngredientsBatchResponse(
        total=len(request.ingredient_ids),
        success=0,
        failed=0,
        skipped=0,
        results=[],
        execution_time_ms=0.0,
    )


@router.post(
    "/scraping/competitor/menu",
    response_model=ScrapeCompetitorMenuResponse,
    status_code=status.HTTP_200_OK,
    summary="Scrape menú de competidor",
    description="""
Scrape el catálogo de productos de un competidor y lo persiste en
`competitor_products`.

**Proceso:**
1. Detecta el scraper asociado al competidor (por URL o nombre).
2. Ejecuta cada término en `search_queries` (o los términos por defecto).
3. Guarda los resultados en DB (upsert por `product_name`).

**Tiempos esperados:** 30 s – 5 min según el número de queries y el rate
limiting configurado para el scraper.
    """,
)
def scrape_competitor_menu(
    request: ScrapeCompetitorMenuRequest,
    db: Session = Depends(get_db),
) -> ScrapeCompetitorMenuResponse:
    try:
        result = _manager(db).scrape_competitor_menu(
            competitor_id=request.competitor_id,
            search_queries=request.search_queries,
            limit_per_query=request.limit_per_query,
        )
        return result
    except Exception as exc:
        logger.error("Unexpected error scraping competitor %d: %s", request.competitor_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@router.post(
    "/scraping/test",
    response_model=TestScraperResponse,
    status_code=status.HTTP_200_OK,
    summary="Test de scraper (sin escritura en DB)",
    description="""
Ejecuta una búsqueda de prueba con el scraper indicado y devuelve los
resultados **sin persistirlos en DB**.

Útil para:
- Validar que los selectores CSS/XPath del YAML son correctos.
- Confirmar que el rate limiting y la navegación funcionan.
- Estimar cuántos productos se pueden extraer antes de activar el scraper.

El campo `error` del response contiene el mensaje de error cuando
`success=false`; no se lanza HTTP 4xx/5xx.
    """,
)
def test_scraper(
    request: TestScraperRequest,
    db: Session = Depends(get_db),
) -> TestScraperResponse:
    started_at = datetime.now()
    mgr = _manager(db)

    try:
        scraper = mgr.get_scraper(request.scraper_id)
    except ScraperNotFoundError as exc:
        return TestScraperResponse(
            success=False,
            scraper_id=request.scraper_id,
            error=f"Scraper not found: {exc.message}",
        )
    except Exception as exc:
        return TestScraperResponse(
            success=False,
            scraper_id=request.scraper_id,
            error=f"Failed to load scraper: {exc}",
        )

    try:
        with scraper:
            products = scraper.search_products(
                query=request.search_query,
                limit=request.limit,
            )
        elapsed_ms = (datetime.now() - started_at).total_seconds() * 1_000

        return TestScraperResponse(
            success=True,
            scraper_id=request.scraper_id,
            business_name=scraper.business_name,
            products_found=len(products),
            products=[
                ScrapedProductResponse(
                    product_name=p.product_name,
                    price=p.price,
                    currency=p.currency,
                    unit=p.unit,
                    category=p.category,
                    url=p.url,
                    image_url=p.image_url,
                    availability=p.availability,
                    metadata=p.metadata,
                )
                for p in products
            ],
            execution_time_ms=elapsed_ms,
        )

    except Exception as exc:
        elapsed_ms = (datetime.now() - started_at).total_seconds() * 1_000
        logger.error("Test scraper '%s' failed: %s", request.scraper_id, exc)
        return TestScraperResponse(
            success=False,
            scraper_id=request.scraper_id,
            business_name=getattr(scraper, "business_name", None),
            execution_time_ms=elapsed_ms,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Bulk operation endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/scraping/all-ingredients",
    response_model=ScrapeIngredientsBatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Scrape todos los ingredientes con source_url",
    description="""
Itera todos los ingredientes activos que tienen `source_url` y actualiza
sus precios.

- `supplier_only=true` omite ingredientes cuyos scrapers son de tipo
  `competitor` (útil para actualizaciones diarias de costos de insumos).
- Procesamiento secuencial; puede tardar varios minutos.
    """,
)
def scrape_all_ingredients(
    supplier_only: bool = False,
    db: Session = Depends(get_db),
) -> ScrapeIngredientsBatchResponse:
    try:
        result = _manager(db).scrape_all_ingredients(supplier_only=supplier_only)
        return result
    except Exception as exc:
        logger.error("Unexpected error in scrape_all_ingredients: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
