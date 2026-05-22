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
    summary="List available scrapers",
    description="""
Returns all scrapers configured in the system, ordered by priority.

Each entry includes:
- **id** / **name**: identifier and human-readable name of the scraped business.
- **type**: `competitor` or `supplier`.
- **scraper_type**: `restaurant`, `retail`, `marketplace`, `custom`.
- **enabled**: whether the scraper is active.
- **priority** / **schedule**: execution order and frequency in batch runs.
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
    summary="Scraper status",
    description="""
Returns status information for a scraper.

> **Note:** Execution statistics (`total_executions`, `success_rate`,
> etc.) are a placeholder until an execution audit table is implemented.
> They are currently returned as zero.
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
    summary="Scrape ingredient price",
    description="""
Navigates to the ingredient's `source_url`, extracts the price and, optionally,
persists it in the database.

**Requirements:**
- The ingredient must exist in DB.
- It must have `source_url` defined.
- A scraper configured for that domain must exist.

**Failure behaviour:**
- Returns `200` with `success=false` and a descriptive `error` (does not raise 4xx/5xx).
- Only raises `500` for completely unexpected errors.
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
    summary="Scrape multiple ingredients (batch)",
    description="""
Launches the scraping of multiple ingredients as a background task.

- The `202 Accepted` response is returned **immediately** with zero counters.
- The actual scraping occurs after the response has been sent.
- Maximum **100** ingredients per request.

> To monitor progress, check the logs or implement a polling endpoint
> with an audit table (roadmap).
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
            bg_db.rollback()
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
    summary="Scrape competitor menu",
    description="""
Scrapes a competitor's product catalogue and persists it in
`competitor_products`.

**Process:**
1. Detects the scraper associated with the competitor (by URL or name).
2. Executes each term in `search_queries` (or the default terms).
3. Saves the results to DB (upsert by `product_name`).

**Expected times:** 30 s – 5 min depending on the number of queries and the
rate limiting configured for the scraper.
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
    summary="Test scraper (no DB writes)",
    description="""
Runs a test search with the specified scraper and returns the results
**without persisting them in DB**.

Useful for:
- Validating that the CSS/XPath selectors in the YAML are correct.
- Confirming that rate limiting and navigation work.
- Estimating how many products can be extracted before activating the scraper.

The `error` field in the response contains the error message when
`success=false`; no HTTP 4xx/5xx is raised.
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
    summary="Scrape all ingredients with source_url",
    description="""
Iterates all active ingredients that have `source_url` and updates
their prices.

- `supplier_only=true` skips ingredients whose scrapers are of type
  `competitor` (useful for daily supply cost updates).
- Sequential processing; may take several minutes.
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
