from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from backend.database import get_db
from backend.models.ingredient import Ingredient
from backend.models.product import Product, ProductSize, SizePackaging
from backend.schemas.product_size import (
    ProductSizeBase,
    ProductSizeCreate,
    ProductSizeResponse,
    SizePackagingCreate,
    SizePackagingResponse,
)

router = APIRouter(prefix="/api", tags=["product_sizes"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_product_or_404(product_id: int, db: Session) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


def _get_size_or_404(size_id: int, db: Session) -> ProductSize:
    size = db.get(ProductSize, size_id)
    if size is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product size not found")
    return size


def _fetch_packaging_response(packaging_id: int, db: Session) -> SizePackagingResponse:
    """Return SizePackagingResponse with packaging_name populated via join."""
    row = (
        db.query(SizePackaging, Ingredient.name.label("packaging_name"))
        .join(Ingredient, SizePackaging.packaging_ingredient_id == Ingredient.id)
        .filter(SizePackaging.id == packaging_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Packaging not found")
    pkg, packaging_name = row
    return SizePackagingResponse.model_validate({
        "id": pkg.id,
        "size_id": pkg.size_id,
        "packaging_ingredient_id": pkg.packaging_ingredient_id,
        "quantity": pkg.quantity,
        "packaging_name": packaging_name,
    })


# ---------------------------------------------------------------------------
# ProductSize endpoints
# ---------------------------------------------------------------------------

@router.get("/products/{product_id}/sizes", response_model=List[ProductSizeResponse])
def list_sizes(product_id: int, db: Session = Depends(get_db)) -> List[ProductSizeResponse]:
    """Return all sizes defined for a product."""
    _get_product_or_404(product_id, db)
    return (
        db.query(ProductSize)
        .filter(ProductSize.product_id == product_id)
        .order_by(ProductSize.scale_factor)
        .all()
    )


@router.post(
    "/products/{product_id}/sizes",
    response_model=ProductSizeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_size(
    product_id: int, body: ProductSizeBase, db: Session = Depends(get_db)
) -> ProductSizeResponse:
    """Add a size variant to a product. product_id is taken from the URL path."""
    _get_product_or_404(product_id, db)
    size = ProductSize(product_id=product_id, **body.model_dump())
    db.add(size)
    db.commit()
    db.refresh(size)
    return size


@router.put("/sizes/{size_id}", response_model=ProductSizeResponse)
def replace_size(
    size_id: int, body: ProductSizeCreate, db: Session = Depends(get_db)
) -> ProductSizeResponse:
    """Replace all fields of a product size. Raises 404 if not found."""
    size = _get_size_or_404(size_id, db)
    for field, value in body.model_dump().items():
        setattr(size, field, value)
    db.commit()
    db.refresh(size)
    return size


@router.delete("/sizes/{size_id}", status_code=status.HTTP_200_OK)
def delete_size(size_id: int, db: Session = Depends(get_db)) -> dict:
    """Delete a product size and its packaging lines (hard delete)."""
    size = _get_size_or_404(size_id, db)
    db.delete(size)
    db.commit()
    return {"message": "Product size deleted"}


# ---------------------------------------------------------------------------
# SizePackaging endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/sizes/{size_id}/packaging",
    response_model=SizePackagingResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_packaging(
    size_id: int, body: SizePackagingCreate, db: Session = Depends(get_db)
) -> SizePackagingResponse:
    """Attach a packaging item (ingredient) to a specific product size."""
    _get_size_or_404(size_id, db)

    if db.get(Ingredient, body.packaging_ingredient_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Packaging ingredient not found")

    pkg = SizePackaging(size_id=size_id, **body.model_dump())
    db.add(pkg)
    db.commit()
    return _fetch_packaging_response(pkg.id, db)


@router.delete("/packaging/{packaging_id}", status_code=status.HTTP_200_OK)
def delete_packaging(packaging_id: int, db: Session = Depends(get_db)) -> dict:
    """Remove a packaging item from a size (hard delete)."""
    pkg = db.get(SizePackaging, packaging_id)
    if pkg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Packaging not found")
    db.delete(pkg)
    db.commit()
    return {"message": "Packaging removed"}
