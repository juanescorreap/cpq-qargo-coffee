from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, aliased

from backend.database import get_db
from backend.models.competitor import (
    Competitor,
    CompetitorPriceObservation,
    CompetitorProduct,
    ProductCompetitorMatch,
)
from backend.models.product import Product, ProductSize


def _latest_obs_map(cp_ids: list[int], db: Session) -> dict[int, CompetitorPriceObservation]:
    """Map competitor_product_id -> its latest price observation."""
    if not cp_ids:
        return {}
    rows = (
        db.query(CompetitorPriceObservation)
        .filter(CompetitorPriceObservation.competitor_product_id.in_(cp_ids))
        .order_by(
            CompetitorPriceObservation.competitor_product_id,
            CompetitorPriceObservation.scraped_at.desc(),
        )
        .distinct(CompetitorPriceObservation.competitor_product_id)
        .all()
    )
    return {o.competitor_product_id: o for o in rows}

router = APIRouter(prefix="/competitors", tags=["UI - Competitors"])

templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _comp_products(competitor_id: int, db: Session) -> list[dict]:
    rows = (
        db.query(CompetitorProduct)
        .filter(CompetitorProduct.competitor_id == competitor_id)
        .order_by(CompetitorProduct.product_name)
        .all()
    )
    obs_map = _latest_obs_map([cp.id for cp in rows], db)
    result = []
    for cp in rows:
        obs = obs_map.get(cp.id)
        result.append({
            "id":               cp.id,
            "product_name":     cp.product_name or "",
            "category":         cp.category or "",
            "size_description": cp.size_description or "",
            "price":            float(obs.price) if obs and obs.price else None,
            "scraped_at":       obs.scraped_at if obs else None,
            "source_url":       (obs.source_url if obs else None) or "",
        })
    return result


def _matches(competitor_id: int, db: Session) -> list[dict]:
    OurProduct = aliased(Product)
    OurSize    = aliased(ProductSize)

    rows = (
        db.query(
            ProductCompetitorMatch,
            OurProduct.name.label("our_product_name"),
            OurSize.size_name.label("our_size_name"),
            CompetitorProduct.product_name.label("comp_product_name"),
            CompetitorProduct.size_description.label("comp_size"),
        )
        .join(OurProduct,        ProductCompetitorMatch.our_product_id      == OurProduct.id)
        .join(OurSize,           ProductCompetitorMatch.our_size_id          == OurSize.id)
        .join(CompetitorProduct, ProductCompetitorMatch.competitor_product_id == CompetitorProduct.id)
        .filter(CompetitorProduct.competitor_id == competitor_id)
        .order_by(OurProduct.name, OurSize.size_name)
        .all()
    )
    obs_map = _latest_obs_map([m.competitor_product_id for m, *_ in rows], db)
    result = []
    for m, our_product_name, our_size_name, comp_product_name, comp_size in rows:
        obs = obs_map.get(m.competitor_product_id)
        result.append({
            "id":                m.id,
            "our_product_name":  our_product_name,
            "our_size_name":     our_size_name or "Base",
            "comp_product_name": comp_product_name or "",
            "comp_size":         comp_size or "",
            "comp_price":        float(obs.price) if obs and obs.price else None,
            "matched_by":        m.matched_by or "",
            "notes":             m.notes or "",
            "matched_at":        m.matched_at,
        })
    return result


def _our_products_json(db: Session) -> list[dict]:
    return [
        {"id": p.id, "name": p.name}
        for p in (
            db.query(Product)
            .filter(Product.is_active == True, Product.is_sub_recipe == False)
            .order_by(Product.name)
            .all()
        )
    ]


def _comp_products_json(comp_products: list[dict]) -> list[dict]:
    return [
        {
            "id":    cp["id"],
            "label": (
                cp["product_name"]
                + (f" — {cp['size_description']}" if cp["size_description"] else "")
                + (f" · ${cp['price']:,.0f}" if cp["price"] else "")
            ),
        }
        for cp in comp_products
    ]


def _product_sizes_map(db: Session) -> dict:
    sizes = (
        db.query(ProductSize)
        .join(Product, ProductSize.product_id == Product.id)
        .filter(Product.is_active == True, Product.is_sub_recipe == False)
        .order_by(ProductSize.scale_factor)
        .all()
    )
    result: dict = {}
    for s in sizes:
        pid = s.product_id
        if pid not in result:
            result[pid] = []
        name = s.size_name or f"×{float(s.scale_factor):.2f}"
        result[pid].append({"id": s.id, "name": name})
    return result


def _detail_ctx(competitor_id: int, db: Session, cp_list: list[dict]) -> dict:
    """Context vars shared by detail page and both partials."""
    return {
        "comp_products":      cp_list,
        "matches":            _matches(competitor_id, db),
        "our_products_json":  _our_products_json(db),
        "comp_products_json": _comp_products_json(cp_list),
        "product_sizes_map":  _product_sizes_map(db),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def competitor_list(
    request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    competitors = (
        db.query(Competitor)
        .order_by(Competitor.name)
        .all()
    )
    return templates.TemplateResponse("competitors/list.html", {
        "request":     request,
        "competitors": competitors,
    })


@router.get("/{competitor_id}", response_class=HTMLResponse)
async def competitor_detail(
    request: Request, competitor_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    competitor = db.get(Competitor, competitor_id)
    if not competitor:
        return HTMLResponse("Competitor not found", status_code=404)

    cp_list = _comp_products(competitor_id, db)
    return templates.TemplateResponse("competitors/detail.html", {
        "request":    request,
        "competitor": competitor,
        "error_cp":   None,
        "error_match": None,
        **_detail_ctx(competitor_id, db, cp_list),
    })


# ── Competitor products (HTMX) ──────────────────────────────────────────────

@router.post("/{competitor_id}/products-htmx", response_class=HTMLResponse)
async def add_comp_product(
    request: Request, competitor_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    competitor = db.get(Competitor, competitor_id)
    form       = await request.form()

    def _render(error: Optional[str] = None) -> HTMLResponse:
        cp_list = _comp_products(competitor_id, db)
        return templates.TemplateResponse("competitors/_comp_products.html", {
            "request":    request,
            "competitor": competitor,
            "comp_products": cp_list,
            "error":      error,
        })

    product_name     = form.get("product_name",     "").strip()
    size_description = form.get("size_description", "").strip()
    raw_price        = form.get("price",            "").strip()

    if not product_name or not size_description or not raw_price:
        return _render("Name, size and price are required.")

    try:
        price = float(raw_price)
        if price < 0:
            return _render("Price cannot be negative.")
    except ValueError:
        return _render("Invalid price.")

    # V2 split: upsert the stable catalog entry, then record an observation.
    cp = (
        db.query(CompetitorProduct)
        .filter(
            CompetitorProduct.competitor_id == competitor_id,
            CompetitorProduct.product_name == product_name,
            CompetitorProduct.size_description == size_description,
        )
        .first()
    )
    if cp is None:
        cp = CompetitorProduct(
            competitor_id    = competitor_id,
            product_name     = product_name,
            category         = form.get("category", "").strip() or None,
            size_description = size_description,
        )
        db.add(cp)
        db.flush()
    db.add(CompetitorPriceObservation(
        competitor_product_id = cp.id,
        price                 = price,
        source_url            = form.get("source_url", "").strip() or None,
    ))
    db.commit()
    return _render()


@router.delete("/{competitor_id}/products-htmx/{cp_id}", response_class=HTMLResponse)
async def delete_comp_product(
    request: Request, competitor_id: int, cp_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    competitor = db.get(Competitor, competitor_id)
    cp = db.get(CompetitorProduct, cp_id)
    if cp and cp.competitor_id == competitor_id:
        db.delete(cp)
        db.commit()

    cp_list = _comp_products(competitor_id, db)
    return templates.TemplateResponse("competitors/_comp_products.html", {
        "request":       request,
        "competitor":    competitor,
        "comp_products": cp_list,
        "error":         None,
    })


# ── Matches (HTMX) ─────────────────────────────────────────────────────────

@router.post("/{competitor_id}/matches-htmx", response_class=HTMLResponse)
async def create_match(
    request: Request, competitor_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    competitor = db.get(Competitor, competitor_id)
    form       = await request.form()
    cp_list    = _comp_products(competitor_id, db)

    def _render(error: Optional[str] = None) -> HTMLResponse:
        return templates.TemplateResponse("competitors/_matches.html", {
            "request":    request,
            "competitor": competitor,
            "error":      error,
            **_detail_ctx(competitor_id, db, cp_list),
        })

    raw_product = form.get("our_product_id",       "").strip()
    raw_size    = form.get("our_size_id",          "").strip()
    raw_cp      = form.get("competitor_product_id","").strip()
    matched_by  = form.get("matched_by",           "").strip()

    if not raw_product or not raw_size or not raw_cp:
        return _render("Select our product, size and competitor product.")
    if not matched_by:
        return _render("The 'Matched by' field is required.")

    our_product_id       = int(raw_product)
    our_size_id          = int(raw_size)
    competitor_product_id = int(raw_cp)

    if db.get(Product,            our_product_id)        is None:
        return _render("Our product not found.")
    if db.get(ProductSize,        our_size_id)           is None:
        return _render("Size not found.")
    if db.get(CompetitorProduct,  competitor_product_id) is None:
        return _render("Competitor product not found.")

    existing = (
        db.query(ProductCompetitorMatch)
        .filter(
            ProductCompetitorMatch.our_product_id        == our_product_id,
            ProductCompetitorMatch.our_size_id            == our_size_id,
            ProductCompetitorMatch.competitor_product_id  == competitor_product_id,
        )
        .first()
    )
    if existing:
        return _render("This match already exists.")

    db.add(ProductCompetitorMatch(
        our_product_id        = our_product_id,
        our_size_id           = our_size_id,
        competitor_product_id = competitor_product_id,
        matched_by            = matched_by,
        notes                 = form.get("notes", "").strip() or None,
    ))
    db.commit()
    return _render()


@router.delete("/{competitor_id}/matches-htmx/{match_id}", response_class=HTMLResponse)
async def delete_match(
    request: Request, competitor_id: int, match_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    competitor = db.get(Competitor, competitor_id)
    match = db.get(ProductCompetitorMatch, match_id)
    if match:
        db.delete(match)
        db.commit()

    cp_list = _comp_products(competitor_id, db)
    return templates.TemplateResponse("competitors/_matches.html", {
        "request":    request,
        "competitor": competitor,
        "error":      None,
        **_detail_ctx(competitor_id, db, cp_list),
    })
