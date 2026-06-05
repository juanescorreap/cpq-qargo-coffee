"""Catalog read queries shared by the JSON API and the UI routers.

FRONTEND_AUDIT #5: routers (e.g. costs.py and costs_ui.py) duplicated the same
"active products / active stores / sizes for a product" SQLAlchemy queries,
coupling each router to the ORM and risking divergence. Centralise them here so
both layers call one source of truth (mirrors how CostCalculator centralises
costing logic).
"""

from __future__ import annotations

from typing import List, Sequence

from sqlalchemy.orm import Session

from backend.models.product import Product, ProductSize
from backend.models.store import Store


def active_products(db: Session) -> List[Product]:
    """All active products, ordered by name."""
    return (
        db.query(Product)
        .filter(Product.is_active == True)  # noqa: E712 — SQLAlchemy boolean
        .order_by(Product.name)
        .all()
    )


def active_products_by_ids(db: Session, product_ids: Sequence[int]) -> List[Product]:
    """Active products restricted to the given ids, ordered by name."""
    if not product_ids:
        return []
    return (
        db.query(Product)
        .filter(Product.id.in_(product_ids), Product.is_active == True)  # noqa: E712
        .order_by(Product.name)
        .all()
    )


def active_stores(db: Session) -> List[Store]:
    """All active stores, ordered by name."""
    return (
        db.query(Store)
        .filter(Store.is_active == True)  # noqa: E712
        .order_by(Store.name)
        .all()
    )


def product_sizes(db: Session, product_id: int) -> List[ProductSize]:
    """Sizes of a product, ordered by scale_factor (small -> large)."""
    return (
        db.query(ProductSize)
        .filter(ProductSize.product_id == product_id)
        .order_by(ProductSize.scale_factor)
        .all()
    )
