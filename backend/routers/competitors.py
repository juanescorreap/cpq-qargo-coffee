from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, aliased

from backend.database import get_db
from backend.models.competitor import Competitor, CompetitorProduct, ProductCompetitorMatch
from backend.models.product import Product, ProductSize
from backend.schemas.competitor import (
    CompetitorCreate,
    CompetitorProductBase,
    CompetitorProductResponse,
    CompetitorResponse,
    CompetitorUpdate,
    ProductCompetitorMatchCreate,
    ProductCompetitorMatchResponse,
)

router = APIRouter(prefix="/api", tags=["competitors"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_competitor_or_404(competitor_id: int, db: Session) -> Competitor:
    c = db.get(Competitor, competitor_id)
    if c is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Competitor not found")
    return c


def _fetch_competitor_product(cp_id: int, db: Session) -> CompetitorProductResponse:
    row = (
        db.query(CompetitorProduct, Competitor.name.label("competitor_name"))
        .join(Competitor, CompetitorProduct.competitor_id == Competitor.id)
        .filter(CompetitorProduct.id == cp_id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Competitor product not found"
        )
    cp, competitor_name = row
    return CompetitorProductResponse.model_validate({
        "id": cp.id,
        "competitor_id": cp.competitor_id,
        "product_name": cp.product_name,
        "category": cp.category,
        "size_description": cp.size_description,
        "price": cp.price,
        "source_url": cp.source_url,
        "scraped_at": cp.scraped_at,
        "competitor_name": competitor_name,
    })


def _fetch_match(match_id: int, db: Session) -> ProductCompetitorMatchResponse:
    OurProduct = aliased(Product)
    OurSize = aliased(ProductSize)

    row = (
        db.query(
            ProductCompetitorMatch,
            OurProduct.name.label("our_product_name"),
            OurSize.size_name.label("our_size_name"),
            CompetitorProduct.product_name.label("competitor_product_name"),
            Competitor.name.label("competitor_name"),
        )
        .join(OurProduct, ProductCompetitorMatch.our_product_id == OurProduct.id)
        .join(OurSize, ProductCompetitorMatch.our_size_id == OurSize.id)
        .join(CompetitorProduct, ProductCompetitorMatch.competitor_product_id == CompetitorProduct.id)
        .join(Competitor, CompetitorProduct.competitor_id == Competitor.id)
        .filter(ProductCompetitorMatch.id == match_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")

    match, our_product_name, our_size_name, competitor_product_name, competitor_name = row
    return _build_match_response(
        match, our_product_name, our_size_name, competitor_product_name, competitor_name
    )


def _build_match_response(
    match: ProductCompetitorMatch,
    our_product_name: Optional[str],
    our_size_name: Optional[str],
    competitor_product_name: Optional[str],
    competitor_name: Optional[str],
) -> ProductCompetitorMatchResponse:
    return ProductCompetitorMatchResponse.model_validate({
        "id": match.id,
        "our_product_id": match.our_product_id,
        "our_size_id": match.our_size_id,
        "competitor_product_id": match.competitor_product_id,
        "matched_by": match.matched_by,
        "notes": match.notes,
        "matched_at": match.matched_at,
        "our_product_name": our_product_name,
        "our_size_name": our_size_name,
        "competitor_product_name": competitor_product_name,
        "competitor_name": competitor_name,
    })


# ---------------------------------------------------------------------------
# Competitor endpoints
# ---------------------------------------------------------------------------

@router.get("/competitors", response_model=List[CompetitorResponse])
def list_competitors(
    is_active: bool = Query(True),
    db: Session = Depends(get_db),
) -> List[CompetitorResponse]:
    """Return all competitors, active by default."""
    return (
        db.query(Competitor)
        .filter(Competitor.is_active == is_active)
        .order_by(Competitor.name)
        .all()
    )


@router.post(
    "/competitors",
    response_model=CompetitorResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_competitor(
    body: CompetitorCreate, db: Session = Depends(get_db)
) -> CompetitorResponse:
    """Create a new competitor."""
    competitor = Competitor(**body.model_dump())
    db.add(competitor)
    db.commit()
    db.refresh(competitor)
    return competitor


@router.put("/competitors/{competitor_id}", response_model=CompetitorResponse)
def update_competitor(
    competitor_id: int, body: CompetitorUpdate, db: Session = Depends(get_db)
) -> CompetitorResponse:
    """Update a competitor. Only provided fields are changed."""
    competitor = _get_competitor_or_404(competitor_id, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(competitor, field, value)
    db.commit()
    db.refresh(competitor)
    return competitor


@router.delete("/competitors/{competitor_id}", status_code=status.HTTP_200_OK)
def deactivate_competitor(competitor_id: int, db: Session = Depends(get_db)) -> dict:
    """Soft-delete a competitor by marking it inactive."""
    competitor = _get_competitor_or_404(competitor_id, db)
    competitor.is_active = False
    db.commit()
    return {"message": "Competitor deactivated"}


# ---------------------------------------------------------------------------
# CompetitorProduct endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/competitors/{competitor_id}/products",
    response_model=List[CompetitorProductResponse],
)
def list_competitor_products(
    competitor_id: int, db: Session = Depends(get_db)
) -> List[CompetitorProductResponse]:
    """Return all scraped products for a competitor."""
    _get_competitor_or_404(competitor_id, db)
    competitor = db.get(Competitor, competitor_id)
    rows = (
        db.query(CompetitorProduct, Competitor.name.label("competitor_name"))
        .join(Competitor, CompetitorProduct.competitor_id == Competitor.id)
        .filter(CompetitorProduct.competitor_id == competitor_id)
        .order_by(CompetitorProduct.product_name)
        .all()
    )
    return [
        CompetitorProductResponse.model_validate({
            "id": cp.id,
            "competitor_id": cp.competitor_id,
            "product_name": cp.product_name,
            "category": cp.category,
            "size_description": cp.size_description,
            "price": cp.price,
            "source_url": cp.source_url,
            "scraped_at": cp.scraped_at,
            "competitor_name": competitor_name,
        })
        for cp, competitor_name in rows
    ]


@router.post(
    "/competitors/{competitor_id}/products",
    response_model=CompetitorProductResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_competitor_product(
    competitor_id: int,
    body: CompetitorProductBase,
    db: Session = Depends(get_db),
) -> CompetitorProductResponse:
    """Add a scraped product for a competitor. competitor_id is taken from the URL path."""
    _get_competitor_or_404(competitor_id, db)
    cp = CompetitorProduct(competitor_id=competitor_id, **body.model_dump())
    db.add(cp)
    db.commit()
    return _fetch_competitor_product(cp.id, db)


@router.delete("/competitor-products/{product_id}", status_code=status.HTTP_200_OK)
def delete_competitor_product(product_id: int, db: Session = Depends(get_db)) -> dict:
    """Delete a scraped competitor product (hard delete)."""
    cp = db.get(CompetitorProduct, product_id)
    if cp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Competitor product not found"
        )
    db.delete(cp)
    db.commit()
    return {"message": "Competitor product deleted"}


# ---------------------------------------------------------------------------
# Match endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/matches",
    response_model=ProductCompetitorMatchResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_match(
    body: ProductCompetitorMatchCreate, db: Session = Depends(get_db)
) -> ProductCompetitorMatchResponse:
    """Manually link one of our product sizes to a competitor product."""
    if db.get(Product, body.our_product_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    if db.get(ProductSize, body.our_size_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product size not found")
    if db.get(CompetitorProduct, body.competitor_product_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Competitor product not found"
        )

    match = ProductCompetitorMatch(**body.model_dump())
    db.add(match)
    db.commit()
    return _fetch_match(match.id, db)


@router.get("/matches", response_model=List[ProductCompetitorMatchResponse])
def list_matches(
    our_product_id: Optional[int] = Query(None, description="Filter by our product"),
    competitor_id: Optional[int] = Query(None, description="Filter by competitor"),
    db: Session = Depends(get_db),
) -> List[ProductCompetitorMatchResponse]:
    """List product-competitor matches with optional filters."""
    OurProduct = aliased(Product)
    OurSize = aliased(ProductSize)

    q = (
        db.query(
            ProductCompetitorMatch,
            OurProduct.name.label("our_product_name"),
            OurSize.size_name.label("our_size_name"),
            CompetitorProduct.product_name.label("competitor_product_name"),
            Competitor.name.label("competitor_name"),
        )
        .join(OurProduct, ProductCompetitorMatch.our_product_id == OurProduct.id)
        .join(OurSize, ProductCompetitorMatch.our_size_id == OurSize.id)
        .join(CompetitorProduct, ProductCompetitorMatch.competitor_product_id == CompetitorProduct.id)
        .join(Competitor, CompetitorProduct.competitor_id == Competitor.id)
    )

    if our_product_id is not None:
        q = q.filter(ProductCompetitorMatch.our_product_id == our_product_id)

    if competitor_id is not None:
        q = q.filter(Competitor.id == competitor_id)

    rows = q.order_by(OurProduct.name, OurSize.size_name).all()

    return [
        _build_match_response(match, our_pname, our_sname, cp_name, comp_name)
        for match, our_pname, our_sname, cp_name, comp_name in rows
    ]


@router.delete("/matches/{match_id}", status_code=status.HTTP_200_OK)
def delete_match(match_id: int, db: Session = Depends(get_db)) -> dict:
    """Delete a product-competitor match (hard delete)."""
    match = db.get(ProductCompetitorMatch, match_id)
    if match is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
    db.delete(match)
    db.commit()
    return {"message": "Match deleted"}
