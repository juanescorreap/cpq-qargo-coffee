from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, aliased

from backend.database import get_db
from backend.models.ingredient import Ingredient
from backend.models.product import Product, ProductSize, RecipeIngredient, RecipeSubRecipe, SizePackaging
from backend.models.recipe_unit import RecipeUnit

router = APIRouter(prefix="/recipes", tags=["UI - Recetas"])

templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)


def get_full_recipe(db: Session, product_id: int) -> dict:
    """Fetch complete recipe data with all joins.

    Returns a dict with three keys:
      - ingredients: recipe ingredient lines with ingredient_name and recipe_unit_name
      - sub_recipes: sub-recipe links with sub_recipe_name
      - sizes: product sizes with their packaging items (packaging_name resolved)

    All Decimal fields are cast to float so the result is directly JSON-serializable
    via Jinja2's tojson filter.
    """
    SubProduct = aliased(Product)

    # ── Ingredient lines ────────────────────────────────────────────────────
    ri_rows = (
        db.query(
            RecipeIngredient,
            Ingredient.name.label("ingredient_name"),
            RecipeUnit.name.label("recipe_unit_name"),
        )
        .join(Ingredient, RecipeIngredient.ingredient_id == Ingredient.id)
        .outerjoin(RecipeUnit, RecipeIngredient.recipe_unit_id == RecipeUnit.id)
        .filter(RecipeIngredient.product_id == product_id)
        .order_by(RecipeIngredient.id)
        .all()
    )

    # ── Sub-recipe links ─────────────────────────────────────────────────────
    rsr_rows = (
        db.query(RecipeSubRecipe, SubProduct.name.label("sub_recipe_name"))
        .join(SubProduct, RecipeSubRecipe.sub_recipe_id == SubProduct.id)
        .filter(RecipeSubRecipe.parent_product_id == product_id)
        .order_by(RecipeSubRecipe.id)
        .all()
    )

    # ── Sizes + their packaging ───────────────────────────────────────────────
    sizes_orm = (
        db.query(ProductSize)
        .filter(ProductSize.product_id == product_id)
        .order_by(ProductSize.scale_factor)
        .all()
    )

    sizes = []
    for size in sizes_orm:
        pkg_rows = (
            db.query(SizePackaging, Ingredient.name.label("packaging_name"))
            .join(Ingredient, SizePackaging.packaging_ingredient_id == Ingredient.id)
            .filter(SizePackaging.size_id == size.id)
            .all()
        )
        sizes.append({
            "id":           size.id,
            "product_id":   size.product_id,
            "size_name":    size.size_name,
            "volume_oz":    float(size.volume_oz) if size.volume_oz else None,
            "scale_factor": float(size.scale_factor),
            "is_default":   size.is_default,
            "_packaging": [
                {
                    "id":                       pkg.id,
                    "size_id":                  pkg.size_id,
                    "packaging_ingredient_id":  pkg.packaging_ingredient_id,
                    "quantity":                 float(pkg.quantity),
                    "packaging_name":           pkg_name,
                }
                for pkg, pkg_name in pkg_rows
            ],
        })

    return {
        "ingredients": [
            {
                "id":                 ri.id,
                "product_id":         ri.product_id,
                "ingredient_id":      ri.ingredient_id,
                "quantity":           float(ri.quantity),
                "recipe_unit_id":     ri.recipe_unit_id,
                "scales_with_size":   ri.scales_with_size,
                "process_yield_loss": float(ri.process_yield_loss),
                "notes":              ri.notes,
                "ingredient_name":    ing_name,
                "recipe_unit_name":   ru_name,
            }
            for ri, ing_name, ru_name in ri_rows
        ],
        "sub_recipes": [
            {
                "id":                 rsr.id,
                "parent_product_id":  rsr.parent_product_id,
                "sub_recipe_id":      rsr.sub_recipe_id,
                "quantity":           float(rsr.quantity),
                "scales_with_size":   rsr.scales_with_size,
                "sub_recipe_name":    sr_name,
            }
            for rsr, sr_name in rsr_rows
        ],
        "sizes": sizes,
    }


@router.get("/builder", response_class=HTMLResponse)
async def recipe_builder(
    request: Request,
    product_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Interactive recipe builder.

    Without product_id renders a product-selector landing page.
    With product_id renders the full builder pre-populated with recipe data.
    """
    products = (
        db.query(Product)
        .filter(Product.is_active == True)
        .order_by(Product.name)
        .all()
    )

    if product_id:
        product = db.get(Product, product_id)
        recipe  = get_full_recipe(db, product_id) if product else None
    else:
        product = None
        recipe  = None

    ingredients = (
        db.query(Ingredient)
        .filter(Ingredient.is_active == True)
        .order_by(Ingredient.name)
        .all()
    )
    recipe_units = (
        db.query(RecipeUnit)
        .filter(RecipeUnit.is_active == True)
        .order_by(RecipeUnit.name)
        .all()
    )
    sub_recipes_available = (
        db.query(Product)
        .filter(Product.is_active == True, Product.is_sub_recipe == True)
        .order_by(Product.name)
        .all()
    )

    return templates.TemplateResponse(
        "recipes/builder.html",
        {
            "request":              request,
            "products":             products,
            "product":              product,
            "recipe":               recipe,
            "ingredients":          ingredients,
            "recipe_units":         recipe_units,
            "sub_recipes_available": sub_recipes_available,
        },
    )
