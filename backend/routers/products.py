from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.product import Product
from backend.schemas.product import ProductCreate, ProductResponse, ProductUpdate

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("", response_model=List[ProductResponse])
def list_products(
    category: Optional[str] = Query(None, description="Filter by category"),
    is_sub_recipe: Optional[bool] = Query(None, description="Filter by sub-recipe flag"),
    is_active: bool = Query(True, description="Filter by active status"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return"),
    db: Session = Depends(get_db),
) -> List[ProductResponse]:
    """Return a paginated list of products with optional filters."""
    q = db.query(Product).filter(Product.is_active == is_active)

    if category is not None:
        q = q.filter(Product.category == category)

    if is_sub_recipe is not None:
        q = q.filter(Product.is_sub_recipe == is_sub_recipe)

    return q.order_by(Product.name).offset(skip).limit(limit).all()


@router.get("/{product_id}", response_model=ProductResponse)
def get_product(product_id: int, db: Session = Depends(get_db)) -> ProductResponse:
    """Return a single product by ID. Raises 404 if not found."""
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(body: ProductCreate, db: Session = Depends(get_db)) -> ProductResponse:
    """Create a new product and return it."""
    product = Product(**body.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.put("/{product_id}", response_model=ProductResponse)
def replace_product(
    product_id: int, body: ProductUpdate, db: Session = Depends(get_db)
) -> ProductResponse:
    """Replace all updatable fields of a product. Raises 404 if not found."""
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    for field, value in body.model_dump().items():
        setattr(product, field, value)

    db.commit()
    db.refresh(product)
    return product


@router.patch("/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: int, body: ProductUpdate, db: Session = Depends(get_db)
) -> ProductResponse:
    """Partially update a product — only provided fields are changed. Raises 404 if not found."""
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(product, field, value)

    db.commit()
    db.refresh(product)
    return product


@router.delete("/{product_id}", status_code=status.HTTP_200_OK)
def deactivate_product(product_id: int, db: Session = Depends(get_db)) -> dict:
    """Soft-delete a product by marking it inactive. Raises 404 if not found."""
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    product.is_active = False
    db.commit()
    return {"message": "Product deactivated"}
