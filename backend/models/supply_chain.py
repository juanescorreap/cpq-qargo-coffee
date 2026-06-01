from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.sql import func

from backend.database import Base


class Region(Base):
    __tablename__ = "regions"

    id: int = Column(Integer, primary_key=True)
    name: str = Column(String(100), nullable=False)
    code: str = Column(String(20), nullable=False, unique=True)
    country_code: str = Column(String(2), nullable=False, default="CO")
    is_active: bool = Column(Boolean, nullable=False, default=True)
    metadata_: object = Column("metadata", JSONB)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Manufacturer(Base):
    __tablename__ = "manufacturers"

    id: int = Column(Integer, primary_key=True)
    name: str = Column(String(200), nullable=False)
    country_code: str = Column(String(2), nullable=False, default="CO")
    tax_id: str | None = Column(String(50))
    website: str | None = Column(Text)
    is_active: bool = Column(Boolean, nullable=False, default=True)
    metadata_: object = Column("metadata", JSONB)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Distributor(Base):
    __tablename__ = "distributors"

    id: int = Column(Integer, primary_key=True)
    name: str = Column(String(200), nullable=False)
    country_code: str = Column(String(2), nullable=False, default="CO")
    tax_id: str | None = Column(String(50))
    contact_email: str | None = Column(String(200))
    contact_phone: str | None = Column(String(50))
    is_active: bool = Column(Boolean, nullable=False, default=True)
    metadata_: object = Column("metadata", JSONB)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SupplyRoute(Base):
    """Abstract supply route: manufacturer → (distributor) → canonical ingredient.

    is_direct=True means the store buys directly from the manufacturer
    (distributor_id must be NULL in that case).
    """

    __tablename__ = "supply_routes"

    __table_args__ = (
        CheckConstraint(
            "is_direct = true OR manufacturer_id IS NOT NULL OR distributor_id IS NOT NULL",
            name="supply_routes_source_check",
        ),
        CheckConstraint(
            "NOT (is_direct = true AND distributor_id IS NOT NULL)",
            name="supply_routes_direct_no_distributor",
        ),
    )

    id: int = Column(Integer, primary_key=True)
    ingredient_id: int = Column(Integer, ForeignKey("ingredients.id"), nullable=False)
    manufacturer_id: int | None = Column(Integer, ForeignKey("manufacturers.id"))
    distributor_id: int | None = Column(Integer, ForeignKey("distributors.id"))
    is_direct: bool = Column(Boolean, nullable=False, default=False)
    is_active: bool = Column(Boolean, nullable=False, default=True)
    metadata_: object = Column("metadata", JSONB)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SupplyRouteAssignment(Base):
    """Assigns a supply route to a region or store with priority and temporal validity.

    store_id NOT NULL → store-level override (takes precedence over regional assignment).
    store_id NULL     → regional assignment (applies to all stores in the region).
    priority 1 = primary, 2 = alternative (activated only if primary fails).
    EXCLUDE constraints (defined in migration) prevent overlapping validity periods.
    """

    __tablename__ = "supply_route_assignments"

    __table_args__ = (
        CheckConstraint(
            "region_id IS NOT NULL OR store_id IS NOT NULL",
            name="sra_scope_required",
        ),
        CheckConstraint(
            "NOT (region_id IS NOT NULL AND store_id IS NOT NULL)",
            name="sra_single_scope",
        ),
        CheckConstraint("priority >= 1", name="sra_priority_positive"),
    )

    id: int = Column(Integer, primary_key=True)
    supply_route_id: int = Column(Integer, ForeignKey("supply_routes.id"), nullable=False)
    region_id: int | None = Column(Integer, ForeignKey("regions.id"))
    store_id: int | None = Column(Integer, ForeignKey("stores.id"))
    priority: int = Column(Integer, nullable=False, default=1)
    valid_from: object = Column(Date, nullable=False, server_default=func.current_date())
    valid_until: object | None = Column(Date)
    change_reason: str | None = Column(String(200))
    assigned_by: str = Column(String(100), nullable=False)
    notes: str | None = Column(Text)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IngredientSupplierRef(Base):
    """External name, code, and purchase unit of a canonical ingredient per supplier."""

    __tablename__ = "ingredient_supplier_refs"

    __table_args__ = (
        UniqueConstraint("supply_route_id", "external_code", name="uq_isr_route_code"),
    )

    id: int = Column(Integer, primary_key=True)
    ingredient_id: int = Column(Integer, ForeignKey("ingredients.id"), nullable=False)
    supply_route_id: int = Column(Integer, ForeignKey("supply_routes.id"), nullable=False)
    external_name: str = Column(String(300), nullable=False)
    external_code: str | None = Column(String(100))
    purchase_unit: str = Column(String(100), nullable=False)
    units_per_pack: object | None = Column(Numeric)
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
        CheckConstraint("purchase_qty > 0 AND recipe_qty > 0", name="suc_quantities_positive"),
        UniqueConstraint("ingredient_ref_id", "recipe_unit_id", name="uq_suc_ref_unit"),
    )

    id: int = Column(Integer, primary_key=True)
    ingredient_ref_id: int = Column(Integer, ForeignKey("ingredient_supplier_refs.id"), nullable=False)
    recipe_unit_id: int = Column(Integer, ForeignKey("recipe_units.id"), nullable=False)
    purchase_qty: object = Column(Numeric, nullable=False)
    recipe_qty: object = Column(Numeric, nullable=False)
    notes: str | None = Column(Text)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SupplyRoutePrice(Base):
    """Price of an ingredient on a supply route, with list and Qargo-negotiated price.

    Prices are immutable — to update, close with valid_until and insert a new row.
    EXCLUDE constraint (defined in migration) prevents overlapping valid periods.
    """

    __tablename__ = "supply_route_prices"

    __table_args__ = (
        CheckConstraint("list_price > 0 AND qargo_price > 0", name="srp_prices_positive"),
        CheckConstraint("qargo_price <= list_price", name="srp_qargo_lte_list"),
        CheckConstraint("currency_code ~ '^[A-Z]{3}$'", name="srp_currency_valid"),
    )

    id: int = Column(Integer, primary_key=True)
    supply_route_id: int = Column(Integer, ForeignKey("supply_routes.id"), nullable=False)
    list_price: object = Column(Numeric, nullable=False)
    qargo_price: object = Column(Numeric, nullable=False)
    currency_code: str = Column(String(3), nullable=False)
    price_per_unit: str = Column(String(100), nullable=False)
    valid_from: object = Column(Date, nullable=False, server_default=func.current_date())
    valid_until: object | None = Column(Date)
    source: str | None = Column(String(100))
    created_by: str = Column(String(100), nullable=False)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IngredientAvailability(Base):
    """Availability status of an ingredient on a route or in a region.

    Observation-only — no decision logic. Enables future predictive models.
    State: 'available' | 'shortage' | 'discontinued' | 'seasonal'.
    """

    __tablename__ = "ingredient_availability"

    __table_args__ = (
        CheckConstraint(
            "supply_route_id IS NOT NULL OR region_id IS NOT NULL",
            name="ia_scope_required",
        ),
        CheckConstraint(
            "status IN ('available', 'shortage', 'discontinued', 'seasonal')",
            name="ia_status_valid",
        ),
        CheckConstraint(
            "expected_resume IS NULL OR status = 'shortage'",
            name="ia_resume_only_for_shortage",
        ),
    )

    id: int = Column(Integer, primary_key=True)
    ingredient_id: int = Column(Integer, ForeignKey("ingredients.id"), nullable=False)
    supply_route_id: int | None = Column(Integer, ForeignKey("supply_routes.id"))
    region_id: int | None = Column(Integer, ForeignKey("regions.id"))
    status: str = Column(String(50), nullable=False)
    expected_resume: object | None = Column(Date)
    valid_from: object = Column(Date, nullable=False, server_default=func.current_date())
    valid_until: object | None = Column(Date)
    reported_by: str | None = Column(String(100))
    notes: str | None = Column(Text)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IngredientSubstitute(Base):
    """Corporate-approved substitute for an ingredient.

    Only corporate can define substitutes (enforced by workflow, recorded in approved_by).
    quantity_ratio: how much substitute to use per unit of the original ingredient.
    """

    __tablename__ = "ingredient_substitutes"

    __table_args__ = (
        CheckConstraint(
            "original_ingredient_id <> substitute_ingredient_id",
            name="is_no_self_substitute",
        ),
        CheckConstraint(
            "activation_condition IN ('shortage', 'unavailable', 'always')",
            name="is_activation_valid",
        ),
        CheckConstraint("quantity_ratio > 0", name="is_ratio_positive"),
        UniqueConstraint(
            "original_ingredient_id", "substitute_ingredient_id", "valid_from",
            name="uq_ingredient_substitute",
        ),
    )

    id: int = Column(Integer, primary_key=True)
    original_ingredient_id: int = Column(Integer, ForeignKey("ingredients.id"), nullable=False)
    substitute_ingredient_id: int = Column(Integer, ForeignKey("ingredients.id"), nullable=False)
    approved_by: str = Column(String(100), nullable=False)
    approval_date: object = Column(Date, nullable=False)
    activation_condition: str = Column(String(50), nullable=False, default="shortage")
    quantity_ratio: object = Column(Numeric, nullable=False, default=1.0)
    recipe_unit_id: int | None = Column(Integer, ForeignKey("recipe_units.id"))
    cost_impact_pct: object | None = Column(Numeric)
    affects_regions: object | None = Column(ARRAY(Integer))
    valid_from: object = Column(Date, nullable=False, server_default=func.current_date())
    valid_until: object | None = Column(Date)
    notes: str | None = Column(Text)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class StoreSupplierHistory(Base):
    """Audit log of which supply route each store used per ingredient over time.

    EXCLUDE constraint (defined in migration) prevents two simultaneous active
    routes for the same store+ingredient — a bug that corrupts cost reports.
    """

    __tablename__ = "store_supplier_history"

    id: int = Column(Integer, primary_key=True)
    store_id: int = Column(Integer, ForeignKey("stores.id"), nullable=False)
    ingredient_id: int = Column(Integer, ForeignKey("ingredients.id"), nullable=False)
    supply_route_id: int = Column(Integer, ForeignKey("supply_routes.id"), nullable=False)
    valid_from: object = Column(Date, nullable=False)
    valid_until: object | None = Column(Date)
    change_reason: str | None = Column(String(200))
    changed_by: str | None = Column(String(100))
    notes: str | None = Column(Text)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RecipeCostSnapshot(Base):
    """Immutable record of a recipe cost calculation per product and store.

    Append-only — never updated. snapshot_detail (JSONB) holds the full
    ingredient-level breakdown so the calculation can be audited without
    recomputing it.
    """

    __tablename__ = "recipe_cost_snapshots"

    __table_args__ = (
        CheckConstraint("base_cost > 0 AND effective_cost > 0", name="rcs_costs_positive"),
    )

    id: int = Column(Integer, primary_key=True)
    product_id: int = Column(Integer, ForeignKey("products.id"), nullable=False)
    store_id: int = Column(Integer, ForeignKey("stores.id"), nullable=False)
    calculated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    base_cost: object = Column(Numeric, nullable=False)
    effective_cost: object = Column(Numeric, nullable=False)
    currency_code: str = Column(String(3), nullable=False)
    has_substitutes: bool = Column(Boolean, nullable=False, default=False)
    snapshot_detail: object = Column(JSONB, nullable=False)
    triggered_by: str | None = Column(String(100))
