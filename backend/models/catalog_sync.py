"""ORM models for the catalog API integration (migration 0033)."""

from sqlalchemy import (
    CHAR,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from backend.database import Base


class StoreCatalogMapping(Base):
    """Maps a CPQ store to its external catalog store_id (one-to-one both ways)."""

    __tablename__ = "store_catalog_mapping"

    id: int = Column(Integer, primary_key=True)
    store_id: int = Column(Integer, ForeignKey("stores.id"), nullable=False, unique=True)
    catalog_store_id: int = Column(Integer, nullable=False, unique=True)
    notes: str | None = Column(Text)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CatalogSyncLog(Base):
    """One row per sync run. Append-only audit."""

    __tablename__ = "catalog_sync_log"

    id: int = Column(Integer, primary_key=True)
    store_id: int | None = Column(Integer, ForeignKey("stores.id"))
    catalog_store_id: int = Column(Integer, nullable=False)
    started_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: object | None = Column(DateTime(timezone=True))
    triggered_by: str = Column(String(50), nullable=False)
    items_fetched: int | None = Column(Integer)
    items_matched: int | None = Column(Integer)
    items_created: int | None = Column(Integer)
    items_updated: int | None = Column(Integer)
    items_skipped: int | None = Column(Integer)
    items_error: int | None = Column(Integer)
    status: str = Column(String(20), nullable=False, server_default="running")
    error_detail: str | None = Column(Text)
    metadata_: object = Column("metadata", JSONB)


class CatalogMatchLog(Base):
    """One row per catalog item processed in a sync. Append-only."""

    __tablename__ = "catalog_match_log"

    id: int = Column(Integer, primary_key=True)
    sync_log_id: int = Column(Integer, ForeignKey("catalog_sync_log.id"), nullable=False)
    catalog_item_id: int = Column(Integer, nullable=False)
    catalog_sku: str | None = Column(String(100))
    catalog_name: str = Column(String(300), nullable=False)
    match_type: str | None = Column(String(20))
    matched_ingredient_id: int | None = Column(Integer, ForeignKey("ingredients.id"))
    fuzzy_score: object | None = Column(Numeric)
    action_taken: str | None = Column(String(20))
    old_price: object | None = Column(Numeric)
    new_price: object | None = Column(Numeric)
    currency_code: str | None = Column(CHAR(3))
    notes: str | None = Column(Text)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
