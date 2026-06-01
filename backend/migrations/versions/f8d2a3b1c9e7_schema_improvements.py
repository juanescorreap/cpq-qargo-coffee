"""Schema improvements: categories FK, NULL-safe pricing uniqueness,
modifier_ingredient_effects, timestamps on recipe tables, composite indexes.

NOTE: Most tables already existed (created via init_db / manual migration before
Alembic was fully wired). This migration therefore uses ALTER TABLE for existing
tables and only CREATE TABLE for the ones that are genuinely new (categories,
modifier_ingredient_effects).

Revision ID: f8d2a3b1c9e7
Revises: 89c7ea73ba3a
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "f8d2a3b1c9e7"
down_revision = "89c7ea73ba3a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. TABLE: categories  (slug as PK — the canonical category key)
    #    This table does NOT exist yet; all others were pre-created.
    # ------------------------------------------------------------------
    op.create_table(
        "categories",
        sa.Column("slug", sa.String(100), primary_key=True),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )

    # Seed from all distinct values already present in both tables so the FK
    # constraints added below never violate referential integrity.
    op.execute("""
        INSERT INTO categories (slug)
        SELECT DISTINCT category FROM products       WHERE category IS NOT NULL
        UNION
        SELECT DISTINCT category FROM category_margins WHERE category IS NOT NULL
        ON CONFLICT (slug) DO NOTHING
    """)

    # ------------------------------------------------------------------
    # 2. FK: products.category → categories.slug
    # ------------------------------------------------------------------
    op.create_foreign_key(
        "fk_products_category",
        "products", "categories",
        ["category"], ["slug"],
    )

    # ------------------------------------------------------------------
    # 3. FK: category_margins.category → categories.slug
    # ------------------------------------------------------------------
    op.create_foreign_key(
        "fk_category_margins_category",
        "category_margins", "categories",
        ["category"], ["slug"],
    )

    # ------------------------------------------------------------------
    # 4. product_pricing: replace the NULL-unsafe UniqueConstraint with
    #    two partial unique indexes that correctly handle nullable store_id.
    #    PostgreSQL UNIQUE constraints allow multiple NULL values (NULL != NULL),
    #    so a standard constraint cannot enforce uniqueness when store_id IS NULL.
    # ------------------------------------------------------------------
    op.drop_constraint("uq_product_pricing", "product_pricing", type_="unique")

    # Partial unique: store-specific prices (store_id NOT NULL)
    op.execute("""
        CREATE UNIQUE INDEX uq_product_pricing_store
        ON product_pricing (product_id, size_id, store_id, effective_date)
        WHERE store_id IS NOT NULL
    """)
    # Partial unique: global prices (store_id IS NULL)
    op.execute("""
        CREATE UNIQUE INDEX uq_product_pricing_global
        ON product_pricing (product_id, size_id, effective_date)
        WHERE store_id IS NULL
    """)
    # Composite lookup index — effective_date DESC optimises "most recent <= today"
    op.execute("""
        CREATE INDEX ix_product_pricing_lookup
        ON product_pricing (product_id, size_id, store_id, effective_date DESC)
    """)

    # ------------------------------------------------------------------
    # 5. TABLE: modifier_ingredient_effects  (NEW)
    #    Replaces the 1:1 columns in modifiers (affects_ingredient_id,
    #    quantity_change) with a proper many-to-many effect table.
    # ------------------------------------------------------------------
    op.create_table(
        "modifier_ingredient_effects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "modifier_id", sa.Integer(),
            sa.ForeignKey("modifiers.id"), nullable=False,
        ),
        sa.Column(
            "ingredient_id", sa.Integer(),
            sa.ForeignKey("ingredients.id"), nullable=False,
        ),
        sa.Column("quantity_change", sa.Numeric(10, 4), nullable=False),
        sa.UniqueConstraint("modifier_id", "ingredient_id", name="uq_modifier_ingredient"),
    )
    op.create_index(
        "ix_modifier_ingredient_effects_modifier_id",
        "modifier_ingredient_effects", ["modifier_id"],
    )

    # Migrate existing data (modifiers table currently has 0 rows, but handle it
    # gracefully for any environment that may have populated it).
    op.execute("""
        INSERT INTO modifier_ingredient_effects (modifier_id, ingredient_id, quantity_change)
        SELECT id, affects_ingredient_id, quantity_change
        FROM   modifiers
        WHERE  affects_ingredient_id IS NOT NULL
        ON CONFLICT (modifier_id, ingredient_id) DO NOTHING
    """)

    # Drop the now-redundant 1:1 columns from modifiers
    op.drop_column("modifiers", "affects_ingredient_id")
    op.drop_column("modifiers", "quantity_change")

    # ------------------------------------------------------------------
    # 6. Timestamps: add created_at / updated_at to three existing tables
    # ------------------------------------------------------------------
    for table in ("recipe_ingredients", "product_sizes", "recipe_sub_recipes"):
        op.add_column(table, sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=True,
        ))
        op.add_column(table, sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=True,
        ))

    # ------------------------------------------------------------------
    # 7. PostgreSQL trigger: auto-stamp updated_at on every UPDATE
    # ------------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION _set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in ("recipe_ingredients", "product_sizes", "recipe_sub_recipes"):
        op.execute(f"""
            CREATE TRIGGER trg_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE PROCEDURE _set_updated_at()
        """)

    # ------------------------------------------------------------------
    # 8. Composite index on competitor_products for time-series queries
    # ------------------------------------------------------------------
    op.create_index(
        "ix_competitor_products_scraping",
        "competitor_products", ["competitor_id", "scraped_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_competitor_products_scraping", "competitor_products")

    for table in ("recipe_ingredients", "product_sizes", "recipe_sub_recipes"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
    op.execute("DROP FUNCTION IF EXISTS _set_updated_at()")

    for table in ("recipe_ingredients", "product_sizes", "recipe_sub_recipes"):
        op.drop_column(table, "updated_at")
        op.drop_column(table, "created_at")

    op.add_column("modifiers", sa.Column(
        "quantity_change", sa.Numeric(10, 4), nullable=True,
    ))
    op.add_column("modifiers", sa.Column(
        "affects_ingredient_id", sa.Integer(),
        sa.ForeignKey("ingredients.id"), nullable=True,
    ))
    op.drop_index("ix_modifier_ingredient_effects_modifier_id", "modifier_ingredient_effects")
    op.drop_table("modifier_ingredient_effects")

    op.execute("DROP INDEX IF EXISTS ix_product_pricing_lookup")
    op.execute("DROP INDEX IF EXISTS uq_product_pricing_global")
    op.execute("DROP INDEX IF EXISTS uq_product_pricing_store")
    op.create_unique_constraint(
        "uq_product_pricing", "product_pricing",
        ["product_id", "size_id", "store_id", "effective_date"],
    )

    op.drop_constraint("fk_category_margins_category", "category_margins", type_="foreignkey")
    op.drop_constraint("fk_products_category", "products", type_="foreignkey")
    op.drop_table("categories")
