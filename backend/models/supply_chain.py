from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from backend.database import Base


class Region(Base):
    __tablename__ = "regions"

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    name: str = Column(String(120), nullable=False)
    code: str = Column(String(40), nullable=False, unique=True)
    country_code: str = Column(String(2), nullable=False, default="CO")  # iso_country
    is_active: bool = Column(Boolean, nullable=False, default=True)
    metadata_: object = Column("metadata", JSONB)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Manufacturer(Base):
    __tablename__ = "manufacturers"

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    name: str = Column(String(160), nullable=False)
    country_code: str = Column(String(2), nullable=False, default="CO")  # iso_country
    tax_id: str | None = Column(String(40))
    website: str | None = Column(Text)
    is_active: bool = Column(Boolean, nullable=False, default=True)
    metadata_: object = Column("metadata", JSONB)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Distributor(Base):
    __tablename__ = "distributors"

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    name: str = Column(String(160), nullable=False)
    country_code: str = Column(String(2), nullable=False, default="CO")  # iso_country
    tax_id: str | None = Column(String(40))
    contact_email: str | None = Column(String(160))
    contact_phone: str | None = Column(String(40))
    is_active: bool = Column(Boolean, nullable=False, default=True)
    metadata_: object = Column("metadata", JSONB)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SupplyRoute(Base):
    """Abstract supply route: manufacturer → (distributor) → canonical ingredient.

    A route must resolve to at least one supplier endpoint (manufacturer or
    distributor). is_direct=True means the store buys directly from the
    manufacturer.
    """

    __tablename__ = "supply_routes"

    __table_args__ = (
        CheckConstraint(
            "is_direct = true OR manufacturer_id IS NOT NULL OR distributor_id IS NOT NULL",
            name="ck_supply_routes_endpoint",
        ),
        CheckConstraint(
            "NOT (is_direct = true AND distributor_id IS NOT NULL)",
            name="ck_supply_routes_direct_no_distributor",
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    ingredient_id: int = Column(
        BigInteger, ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False
    )
    manufacturer_id: int | None = Column(
        BigInteger, ForeignKey("manufacturers.id", ondelete="SET NULL")
    )
    distributor_id: int | None = Column(
        BigInteger, ForeignKey("distributors.id", ondelete="SET NULL")
    )
    is_direct: bool = Column(Boolean, nullable=False, default=False)
    is_active: bool = Column(Boolean, nullable=False, default=True)
    metadata_: object = Column("metadata", JSONB)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SupplyRouteAssignment(Base):
    """Assigns a supply route to a region or store with priority and temporal validity.

    Scope must target either a region or a store (or both for store-in-region
    overrides). priority 1 = primary, 2 = alternative.
    """

    __tablename__ = "supply_route_assignments"

    __table_args__ = (
        CheckConstraint(
            "region_id IS NOT NULL OR store_id IS NOT NULL",
            name="ck_sra_scope",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from",
            name="ck_sra_validity",
        ),
        CheckConstraint("priority >= 1", name="ck_sra_priority_positive"),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    supply_route_id: int = Column(
        BigInteger, ForeignKey("supply_routes.id", ondelete="CASCADE"), nullable=False
    )
    region_id: int | None = Column(BigInteger, ForeignKey("regions.id", ondelete="SET NULL"))
    store_id: int | None = Column(BigInteger, ForeignKey("stores.id", ondelete="SET NULL"))
    priority: int = Column(Integer, nullable=False, default=1)
    valid_from: object = Column(Date, nullable=False, server_default=func.current_date())
    valid_until: object | None = Column(Date)
    change_reason: str | None = Column(String(160))
    assigned_by: str = Column(String(120), nullable=False)
    notes: str | None = Column(Text)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IngredientSupplierRef(Base):
    """External name, code, and purchase unit of a canonical ingredient per route."""

    __tablename__ = "ingredient_supplier_refs"

    __table_args__ = (
        UniqueConstraint("ingredient_id", "supply_route_id", name="uq_isr_refs"),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    ingredient_id: int = Column(
        BigInteger, ForeignKey("ingredients.id", ondelete="CASCADE"), nullable=False
    )
    supply_route_id: int = Column(
        BigInteger, ForeignKey("supply_routes.id", ondelete="CASCADE"), nullable=False
    )
    external_name: str = Column(String(180), nullable=False)
    external_code: str | None = Column(String(80))
    purchase_unit: str = Column(String(40), nullable=False)
    units_per_pack: object | None = Column(Numeric(14, 6))  # quantity_amount
    is_active: bool = Column(Boolean, nullable=False, default=True)
    notes: str | None = Column(Text)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SupplierUnitConversion(Base):
    """Converts the supplier purchase unit to the canonical Qargo recipe unit.

    purchase_qty units from supplier = recipe_qty units in the recipe.
    Example: purchase_qty=1 (bag 5 kg) = recipe_qty=5000 (grams).
    """

    __tablename__ = "supplier_unit_conversions"

    __table_args__ = (
        UniqueConstraint("ingredient_ref_id", "recipe_unit_id", name="uq_suc"),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    ingredient_ref_id: int = Column(
        BigInteger, ForeignKey("ingredient_supplier_refs.id", ondelete="CASCADE"), nullable=False
    )
    recipe_unit_id: int = Column(
        BigInteger, ForeignKey("recipe_units.id", ondelete="RESTRICT"), nullable=False
    )
    purchase_qty: object = Column(Numeric(14, 6), nullable=False)  # quantity_amount
    recipe_qty: object = Column(Numeric(14, 6), nullable=False)    # quantity_amount
    notes: str | None = Column(Text)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SupplyRoutePrice(Base):
    """Effective-dated price of an ingredient on a supply route.

    list_price and qargo_price are price_amount (non-negative). qargo_price
    cannot exceed list_price. The EXCLUDE constraint (in the migration) forbids
    two overlapping price windows for the same route.
    """

    __tablename__ = "supply_route_prices"

    __table_args__ = (
        CheckConstraint("qargo_price <= list_price", name="ck_srp_qargo_lte_list"),
        CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from", name="ck_srp_validity"
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    supply_route_id: int = Column(
        BigInteger, ForeignKey("supply_routes.id", ondelete="CASCADE"), nullable=False
    )
    list_price: object = Column(Numeric(14, 4), nullable=False)   # price_amount
    qargo_price: object = Column(Numeric(14, 4), nullable=False)  # price_amount
    currency_code: str = Column(
        String(3),
        ForeignKey("currencies.code", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
    )
    price_per_unit: str = Column(String(40), nullable=False)  # DEPRECATED: use price_unit_id
    price_unit_id: int | None = Column(
        BigInteger, ForeignKey("recipe_units.id", ondelete="RESTRICT")
    )
    valid_from: object = Column(Date, nullable=False, server_default=func.current_date())
    valid_until: object | None = Column(Date)
    source: str | None = Column(String(120))
    created_by: str = Column(String(120), nullable=False)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IngredientAvailability(Base):
    """Availability status of an ingredient on a route or in a region.

    Observation-only — no decision logic. Enables future predictive models.
    State: 'available' | 'shortage' | 'discontinued' | 'seasonal'.
    """

    __tablename__ = "ingredient_availability"

    __table_args__ = (
        CheckConstraint(
            "status IN ('available', 'shortage', 'discontinued', 'seasonal')",
            name="ck_ia_status",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from", name="ck_ia_validity"
        ),
        CheckConstraint(
            "supply_route_id IS NOT NULL OR region_id IS NOT NULL", name="ck_ia_scope"
        ),
        CheckConstraint(
            "expected_resume IS NULL OR status = 'shortage'",
            name="ck_ia_resume_only_for_shortage",
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    ingredient_id: int = Column(
        BigInteger, ForeignKey("ingredients.id", ondelete="CASCADE"), nullable=False
    )
    supply_route_id: int | None = Column(
        BigInteger, ForeignKey("supply_routes.id", ondelete="CASCADE")
    )
    region_id: int | None = Column(BigInteger, ForeignKey("regions.id", ondelete="CASCADE"))
    status: str = Column(String(20), nullable=False)
    expected_resume: object | None = Column(Date)
    valid_from: object = Column(Date, nullable=False, server_default=func.current_date())
    valid_until: object | None = Column(Date)
    reported_by: str | None = Column(String(120))
    notes: str | None = Column(Text)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IngredientSubstitute(Base):
    """Corporate-approved substitute for an ingredient.

    quantity_ratio: how much substitute to use per unit of the original
    ingredient. Regions where the substitute applies live in the junction
    table ``ingredient_substitute_regions`` (empty = global).
    """

    __tablename__ = "ingredient_substitutes"

    __table_args__ = (
        CheckConstraint(
            "original_ingredient_id <> substitute_ingredient_id",
            name="ck_ingredient_substitutes_no_self",
        ),
        CheckConstraint(
            "activation_condition IN ('shortage', 'unavailable', 'always')",
            name="ck_ingredient_substitutes_activation",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from",
            name="ck_ingredient_substitutes_validity",
        ),
        # Temporal EXCLUDE (no_overlap_isub) defined in migration 0006: forbids
        # overlapping validity windows for the same (original, substitute) pair
        # while allowing re-approval over time.
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    original_ingredient_id: int = Column(
        BigInteger, ForeignKey("ingredients.id", ondelete="CASCADE"), nullable=False
    )
    substitute_ingredient_id: int = Column(
        BigInteger, ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False
    )
    approved_by: str = Column(String(120), nullable=False)
    approval_date: object = Column(Date, nullable=False)
    activation_condition: str = Column(String(20), nullable=False, default="shortage")
    quantity_ratio: object = Column(Numeric(14, 6), nullable=False, default=1.0)  # quantity_amount
    recipe_unit_id: int | None = Column(
        BigInteger, ForeignKey("recipe_units.id", ondelete="SET NULL")
    )
    cost_impact_pct: object | None = Column(Numeric(6, 3))  # pct_amount
    valid_from: object = Column(Date, nullable=False, server_default=func.current_date())
    valid_until: object | None = Column(Date)
    notes: str | None = Column(Text)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class StoreSupplierHistory(Base):
    """Audit log of which supply route each store used per ingredient over time."""

    __tablename__ = "store_supplier_history"

    __table_args__ = (
        CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from", name="ck_ssh_validity"
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    store_id: int = Column(
        BigInteger, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    ingredient_id: int = Column(
        BigInteger, ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False
    )
    supply_route_id: int = Column(
        BigInteger, ForeignKey("supply_routes.id", ondelete="RESTRICT"), nullable=False
    )
    valid_from: object = Column(Date, nullable=False)
    valid_until: object | None = Column(Date)
    change_reason: str | None = Column(String(160))
    changed_by: str | None = Column(String(120))
    notes: str | None = Column(Text)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RecipeCostSnapshot(Base):
    """Immutable, point-in-time recipe cost freeze (append-only, partitioned).

    Range-partitioned on ``calculated_at`` in PostgreSQL, so the primary key is
    composite ``(id, calculated_at)``. snapshot_detail (JSONB) holds the full
    ingredient-level breakdown.
    """

    __tablename__ = "recipe_cost_snapshots"

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    product_id: int = Column(
        BigInteger, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    store_id: int = Column(
        BigInteger, ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False
    )
    base_cost: object = Column(Numeric(14, 4), nullable=False)       # price_amount
    effective_cost: object = Column(Numeric(14, 4), nullable=False)  # price_amount
    currency_code: str = Column(
        String(3),
        ForeignKey("currencies.code", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
    )
    has_substitutes: bool = Column(Boolean, nullable=False, default=False)
    snapshot_detail: object = Column(JSONB, nullable=False)
    triggered_by: str | None = Column(String(120))
    calculated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), primary_key=True
    )
